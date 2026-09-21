<?xml version="1.0" encoding="utf-8"?>
<!-- Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
     docs/base-images.md §3 (Windows) and §4 (direct ISO boot). Mirrors
     iso/answer-files/rhel-family/ks-rocky9.cfg.tpl's role for Linux:
     partitioning, network, and first-boot bootstrap (WinRM here, an
     SSH key there) all live in this one rendered file.

     Disk bus is SATA and the NIC model is e1000e (not virtio) on
     purpose — both have in-box Windows Server 2022 drivers, avoiding
     the virtio-win driver-injection dance entirely for this first
     pass. Revisit if VM disk/network performance actually matters.

     Install image index 1 = "Windows Server 2022 SERVERSTANDARDCORE"
     (no Desktop Experience) — confirmed via `wiminfo` against the
     real eval ISO staged for this project; Ansible/WinRM manages the
     host, so no GUI is needed. -->
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">

  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <SetupUILanguage>
        <UILanguage>en-US</UILanguage>
      </SetupUILanguage>
      <InputLocale>en-US</InputLocale>
      <SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage>
      <UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <UserData>
        <AcceptEula>true</AcceptEula>
      </UserData>
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>/IMAGE/INDEX</Key>
              <Value>1</Value>
            </MetaData>
          </InstallFrom>
          <InstallTo>
            <DiskID>0</DiskID>
            <PartitionID>1</PartitionID>
          </InstallTo>
        </OSImage>
      </ImageInstall>
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order>
              <Type>Primary</Type>
              <Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order>
              <PartitionID>1</PartitionID>
              <Format>NTFS</Format>
              <Label>OS</Label>
              <Active>true</Active>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
        <WillShowUI>OnError</WillShowUI>
      </DiskConfiguration>
    </component>
  </settings>

  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>${hostname}</ComputerName>
      <TimeZone>UTC</TimeZone>
    </component>
  </settings>

  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <UserAccounts>
        <AdministratorPassword>
          <Value>${windows_admin_password}</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>
      </UserAccounts>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <NetworkLocation>Work</NetworkLocation>
        <ProtectYourPC>3</ProtectYourPC>
        <SkipMachineOOBE>true</SkipMachineOOBE>
        <SkipUserOOBE>true</SkipUserOOBE>
      </OOBE>
      <TimeZone>UTC</TimeZone>
      <!-- FirstLogonCommands only fires after an actual logon — auto-
           logon once (as Administrator) so this runs fully unattended
           with nobody physically present, matching the same "no
           interactive step needed" contract as the Linux kickstart's
           %post. (RunSynchronousCommand under a specialize-pass
           component was tried first and rejected by Setup twice under
           different components — hrResult 0x80220001, confirmed
           against C:\Windows\Panther\setupact.log both times — this
           is the well-documented, reliable alternative.) -->
      <AutoLogon>
        <Enabled>true</Enabled>
        <LogonCount>1</LogonCount>
        <Username>Administrator</Username>
        <Password>
          <Value>${windows_admin_password}</Value>
          <PlainText>true</PlainText>
        </Password>
      </AutoLogon>
      <FirstLogonCommands>
%{ if addressing.mode == "static" ~}
        <SynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Description>Set static IP</Description>
          <CommandLine>netsh interface ip set address name="Ethernet" static ${addressing.address} ${cidrnetmask("${addressing.address}/${addressing.prefix_length}")} ${addressing.gateway}</CommandLine>
        </SynchronousCommand>
        <SynchronousCommand wcm:action="add">
          <Order>2</Order>
          <Description>Set DNS</Description>
          <!-- No dedicated DNS field in environment.yml yet — same
               gateway-as-resolver default as the Linux kickstart
               (iso/answer-files/rhel-family/ks-rocky9.cfg.tpl). -->
          <CommandLine>netsh interface ip set dns name="Ethernet" static ${addressing.gateway}</CommandLine>
        </SynchronousCommand>
%{ endif ~}
        <SynchronousCommand wcm:action="add">
          <Order>3</Order>
          <Description>Enable PSRemoting</Description>
          <CommandLine>powershell -NoProfile -Command "Enable-PSRemoting -Force -SkipNetworkProfileCheck"</CommandLine>
        </SynchronousCommand>
        <SynchronousCommand wcm:action="add">
          <Order>4</Order>
          <Description>WinRM quickconfig</Description>
          <CommandLine>winrm quickconfig -quiet</CommandLine>
        </SynchronousCommand>
        <SynchronousCommand wcm:action="add">
          <Order>5</Order>
          <Description>Create self-signed cert and HTTPS listener</Description>
          <!-- Same reasoning as docs/install-opentofu-windows-wsl.md
               §3 (WinRM HTTPS on the Hyper-V host) applied here to the
               guest: self-signed is normal for a self-hosted lab. -->
          <CommandLine>powershell -NoProfile -Command "$c = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation Cert:\LocalMachine\My; New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * -CertificateThumbPrint $c.Thumbprint -Force"</CommandLine>
        </SynchronousCommand>
        <SynchronousCommand wcm:action="add">
          <Order>6</Order>
          <Description>Open WinRM HTTPS firewall port</Description>
          <CommandLine>powershell -NoProfile -Command "New-NetFirewallRule -DisplayName 'WinRM HTTPS' -Name WinRMHTTPSIn -Profile Any -LocalPort 5986 -Protocol TCP -Action Allow%{ if management_source != "" }  -RemoteAddress ${management_source}%{ endif }"</CommandLine>
        </SynchronousCommand>
        <SynchronousCommand wcm:action="add">
          <Order>7</Order>
          <Description>Allow Basic auth over HTTPS for the first Ansible connection</Description>
          <CommandLine>winrm set winrm/config/service/auth "@{Basic=\"true\"}"</CommandLine>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>

</unattend>
