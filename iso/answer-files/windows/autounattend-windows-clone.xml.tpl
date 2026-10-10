<?xml version="1.0" encoding="utf-8"?>
<!-- First-boot answer file for a VM cloned from a sysprepped Windows
     template (image_source = "packer_template"), rendered by
     tofu/modules/vm/libvirt/main.tf and delivered on a CD. A template is
     generalized with `sysprep /generalize /oobe`, so on first boot Setup
     runs specialize + oobeSystem again and finds this file at the root of
     the removable media (the implicit Autounattend.xml search). It's the
     specialize/oobeSystem half of autounattend-windows-server.xml.tpl
     (identical in the client template apart from comments) - no windowsPE
     pass, since nothing is installed - plus a step that extends C: when
     the clone's disk is bigger than the template's. One file for every
     Windows version. -->
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">

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
          <!-- Same reasoning as User_Docs/Install-OpenTofu-WSL.md
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
        <SynchronousCommand wcm:action="add">
          <Order>8</Order>
          <Description>Extend C: into a clone's larger disk</Description>
          <CommandLine>powershell -NoProfile -Command "$p = Get-Partition -DriveLetter C; $max = (Get-PartitionSupportedSize -DriveLetter C).SizeMax; if ($max -gt $p.Size) { Resize-Partition -DriveLetter C -Size $max }"</CommandLine>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>

</unattend>
