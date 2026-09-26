<?xml version="1.0" encoding="utf-8"?>
<!-- Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
     Claude_Docs/Design_Base-Images.md §3 (Windows) and §4 (direct ISO boot). Windows
     10 counterpart to autounattend-windows-client.xml.tpl — identical
     in every respect except the /IMAGE/NAME value below ("Windows 10
     Pro" vs "Windows 11 Pro"), since client ISOs select an edition by
     name and the two products' image names differ. Not folded into
     one shared template with a variable for the image name purely to
     keep with this repo's existing convention (Server's three versions
     share one template because nothing in the XML is actually version-
     specific there; Client's is a real per-product difference, so gets
     its own file, matching how Windows 11's own template split from
     Server's).

     The LabConfig registry bypass block (windowsPE pass) below is kept
     even though Windows 10 doesn't enforce the TPM 2.0/Secure Boot/CPU-
     allowlist checks Windows 11 does — it's a no-op here, not a
     correctness risk, and keeping it means this file mirrors the
     Windows 11 template closely enough that a future fix to one is easy
     to eyeball-diff against the other. UNVERIFIED against real
     hardware as of this writing — see
     autounattend-windows-client.xml.tpl's own comment for the same
     caveats (exact /IMAGE/NAME string unconfirmed via `wiminfo` against
     the actual staged ISO; expect iteration). -->
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
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassTPMCheck /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>2</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassSecureBootCheck /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>3</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassRAMCheck /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>4</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassCPUCheck /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>5</Order>
          <Path>reg add HKLM\SYSTEM\Setup\LabConfig /v BypassNRO /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
      </RunSynchronous>
      <UserData>
        <ProductKey>
          <Key></Key>
          <WillShowUI>Never</WillShowUI>
        </ProductKey>
        <AcceptEula>true</AcceptEula>
      </UserData>
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>/IMAGE/NAME</Key>
              <Value>Windows 10 Pro</Value>
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
      <!-- Same AutoLogon-once + FirstLogonCommands approach as the
           Server template, for the same reason (RunSynchronousCommand
           under specialize was rejected by Setup there — see that
           file's own comment). -->
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
