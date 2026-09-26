<?xml version="1.0" encoding="utf-8"?>
<!-- Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
     Claude_Docs/Design_Base-Images.md §3 (Windows) and §4 (direct ISO boot). Client
     counterpart to autounattend-windows-server.xml.tpl — same overall
     shape (same template vars: hostname, windows_admin_password,
     addressing, management_source; same FirstLogonCommands WinRM
     bootstrap; same SATA/e1000e device choice for in-box drivers), but
     two real differences from Server, both UNVERIFIED against real
     hardware as of this writing (M5's workstation-support pass) —
     expect this to need iteration the same way the Server template did
     (Claude_Docs/Testing_Troubleshooting-Log.md's ten-round answer-file saga):

     1. Image selection by NAME, not INDEX. A client ISO ships multiple
        SKU images (Home/Pro/Education/...) in one install.wim/.esd,
        unlike Server's single eval-index image — and Home CANNOT join
        an AD domain at all, so this must resolve to Pro or higher.
        "Windows 11 Pro" below is the commonly-documented image name on
        Microsoft's standard multi-edition retail/VL media; confirm the
        exact string via `wiminfo` against the actual staged ISO
        (mirrors how the Server template's own shared-template claim was
        verified) before trusting it — an exact-string mismatch is the
        single most likely first-attempt failure point for this file.

     2. A LabConfig registry bypass block (windowsPE pass, first child
        of Microsoft-Windows-Setup, before UserData/ImageInstall/
        DiskConfiguration — matching common community-documented
        unattended-Windows-11 answer files, though this exact placement
        hasn't been hands-on confirmed in THIS repo yet). This VM won't
        meet Windows 11's TPM 2.0/Secure Boot/CPU-allowlist/RAM
        requirements by default, and 22H2+ also enforces an internet-
        connection/Microsoft-account OOBE gate that the
        HideOnlineAccountScreens/NetworkLocation/ProtectYourPC flags
        below (inherited from Server, where they're sufficient) don't
        reliably suppress alone on Client — BypassNRO addresses that
        specifically. This is a well-known, widely-referenced community
        workaround, not a Microsoft-published/stable mechanism; treat
        "works on the first Windows 11 build tested" as provisional. -->
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
              <Value>Windows 11 Pro</Value>
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
