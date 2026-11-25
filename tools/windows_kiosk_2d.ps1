[CmdletBinding()]
param(
    [ValidateSet('Status', 'Enable', 'Disable')]
    [string]$Action = 'Status',
    [string]$Installation,
    [string]$StaffPasswordHash = (Join-Path $env:LOCALAPPDATA 'AI-decanat-2D\admin.hash')
)

$ErrorActionPreference = 'Stop'
$userName = 'DecanatKiosk'
$statePath = 'HKLM:\SOFTWARE\AI-decanat\Kiosk2D'
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$winlogonNames = @('DefaultUserName', 'DefaultDomainName', 'AutoAdminLogon')
$logonSecret = 'DefaultPassword'
$backupSecret = 'AI-decanat-Kiosk2D-PreviousDefaultPassword'
$taskName = 'AI-decanat-Kiosk2D-Autologon'
$systemPolicy = 'Software\Microsoft\Windows\CurrentVersion\Policies\System'
$state = Get-ItemProperty -LiteralPath $statePath -ErrorAction SilentlyContinue
$user = Get-LocalUser -Name $userName -ErrorAction SilentlyContinue
$logon = Get-ItemProperty -LiteralPath $winlogon

if ($Action -eq 'Status') {
    [pscustomobject]@{
        Account = $userName
        Managed = [bool]($state -and $user -and $state.Sid -eq $user.SID.Value)
        AccountEnabled = [bool]($user -and $user.Enabled)
        AutoLogon = [bool]($logon.AutoAdminLogon -eq '1' -and $logon.DefaultUserName -eq $userName)
        Installation = $state.Installation
        Provisioned = [bool]($state.Provisioned -eq 1)
    }
    return
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this command from 64-bit PowerShell as a Windows administrator.'
}
if ($state -and (-not $user -or $state.Sid -ne $user.SID.Value)) {
    throw 'The kiosk record and local account do not match.'
}

if (-not ('DecanatKioskNative' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security;
using System.Text;

public static class DecanatKioskNative {
    [DllImport("userenv.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    public static extern int CreateProfile(string sid, string name, [Out] StringBuilder path, uint size);

    [StructLayout(LayoutKind.Sequential)]
    private struct UnicodeString {
        public ushort Length, MaximumLength;
        public IntPtr Buffer;

        public UnicodeString(int length, IntPtr buffer) {
            Length = checked((ushort)(length * 2));
            MaximumLength = checked((ushort)(Length + 2));
            Buffer = buffer;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ObjectAttributes {
        public uint Length;
        public IntPtr RootDirectory, ObjectName;
        public uint Attributes;
        public IntPtr SecurityDescriptor, SecurityQualityOfService;
    }

    [DllImport("advapi32.dll")]
    private static extern uint LsaOpenPolicy(IntPtr system, ref ObjectAttributes attributes, uint access, out IntPtr handle);
    [DllImport("advapi32.dll")]
    private static extern uint LsaStorePrivateData(IntPtr handle, ref UnicodeString key, ref UnicodeString data);
    [DllImport("advapi32.dll")]
    private static extern uint LsaStorePrivateData(IntPtr handle, ref UnicodeString key, IntPtr data);
    [DllImport("advapi32.dll")]
    private static extern uint LsaRetrievePrivateData(IntPtr handle, ref UnicodeString key, out IntPtr data);
    [DllImport("advapi32.dll")]
    private static extern uint LsaFreeMemory(IntPtr buffer);
    [DllImport("advapi32.dll")]
    private static extern uint LsaNtStatusToWinError(uint status);
    [DllImport("advapi32.dll")]
    private static extern uint LsaClose(IntPtr handle);

    public static bool ManagePassword(string keyName, SecureString password, bool read, string copyTo) {
        const uint missing = 0xC0000034;
        const uint getPrivateInformation = 4, createSecret = 0x20;
        var attributes = new ObjectAttributes { Length = (uint)Marshal.SizeOf(typeof(ObjectAttributes)) };
        IntPtr handle;
        uint status = LsaOpenPolicy(IntPtr.Zero, ref attributes, getPrivateInformation | createSecret, out handle);
        if (status != 0) throw new Win32Exception((int)LsaNtStatusToWinError(status));
        IntPtr keyBuffer = IntPtr.Zero, passwordBuffer = IntPtr.Zero, copyKeyBuffer = IntPtr.Zero;
        try {
            keyBuffer = Marshal.StringToHGlobalUni(keyName);
            var key = new UnicodeString(keyName.Length, keyBuffer);
            if (read) {
                IntPtr value;
                status = LsaRetrievePrivateData(handle, ref key, out value);
                if (status == missing) return false;
                if (status == 0) {
                    try {
                        if (copyTo != null) {
                            copyKeyBuffer = Marshal.StringToHGlobalUni(copyTo);
                            var copyKey = new UnicodeString(copyTo.Length, copyKeyBuffer);
                            var stored = (UnicodeString)Marshal.PtrToStructure(value, typeof(UnicodeString));
                            status = LsaStorePrivateData(handle, ref copyKey, ref stored);
                        }
                    } finally {
                        if (value != IntPtr.Zero) LsaFreeMemory(value);
                    }
                }
            } else if (password == null) {
                status = LsaStorePrivateData(handle, ref key, IntPtr.Zero);
                if (status == missing) return false;
            } else {
                passwordBuffer = Marshal.SecureStringToGlobalAllocUnicode(password);
                var data = new UnicodeString(password.Length, passwordBuffer);
                status = LsaStorePrivateData(handle, ref key, ref data);
            }
            if (status != 0) throw new Win32Exception((int)LsaNtStatusToWinError(status));
            return true;
        } finally {
            if (passwordBuffer != IntPtr.Zero) Marshal.ZeroFreeGlobalAllocUnicode(passwordBuffer);
            if (keyBuffer != IntPtr.Zero) Marshal.FreeHGlobal(keyBuffer);
            if (copyKeyBuffer != IntPtr.Zero) Marshal.FreeHGlobal(copyKeyBuffer);
            LsaClose(handle);
        }
    }
}
'@
}

function Invoke-Icacls {
    & "$env:SystemRoot\System32\icacls.exe" @args | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'icacls failed.' }
}

function Set-KioskShell([string]$Profile, [string]$Sid, [string]$Shell) {
    $reg = "$env:SystemRoot\System32\reg.exe"
    $hive = $Sid
    $loadedHere = -not (Test-Path -LiteralPath "Registry::HKEY_USERS\$Sid")
    if ($loadedHere) {
        $hive = "DecanatKiosk-$PID"
        & $reg load "HKU\$hive" (Join-Path $Profile 'NTUSER.DAT') | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Unable to load the kiosk user profile.' }
    }
    try {
        $policy = [Microsoft.Win32.Registry]::Users.CreateSubKey("$hive\$systemPolicy")
        try {
            if ($Shell) {
                $policy.SetValue('Shell', $Shell, 'String')
                $policy.SetValue('DisableTaskMgr', 1, 'DWord')
            } else {
                $policy.DeleteValue('Shell', $false)
                $policy.DeleteValue('DisableTaskMgr', $false)
            }
        } finally {
            $policy.Dispose()
        }
    } finally {
        if ($loadedHere) {
            & $reg unload "HKU\$hive" | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Unable to unload the kiosk user profile.' }
        }
    }
}

function Get-ProfilePath([string]$Sid) {
    $entry = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$Sid"
    $profile = Get-ItemPropertyValue -LiteralPath $entry -Name ProfileImagePath -ErrorAction SilentlyContinue
    if ($profile) { [Environment]::ExpandEnvironmentVariables($profile) }
}

function Restore-Logon([bool]$RemoveSecret, [bool]$RestoreSecret) {
    Set-ItemProperty -LiteralPath $winlogon -Name AutoAdminLogon -Value '0'
    if ($RestoreSecret) {
        if (-not [DecanatKioskNative]::ManagePassword($backupSecret, $null, $true, $logonSecret)) {
            throw 'The previous Windows autologon secret could not be restored.'
        }
        [void][DecanatKioskNative]::ManagePassword($backupSecret, $null, $false, $null)
    } elseif ($RemoveSecret) {
        [void][DecanatKioskNative]::ManagePassword($logonSecret, $null, $false, $null)
    }
    foreach ($name in $winlogonNames) {
        $previous = $state.PSObject.Properties["Previous$name"]
        if ($previous) {
            Set-ItemProperty -LiteralPath $winlogon -Name $name -Value $previous.Value
        } else {
            Remove-ItemProperty -LiteralPath $winlogon -Name $name -ErrorAction SilentlyContinue
        }
    }
}

function Register-KioskLogonTask {
    # Interactive staff sign-in can replace DefaultUserName. Reassert the kiosk target
    # after logon so maintenance sessions do not break the next automatic sign-in.
    $repair = @'
$state = Get-ItemProperty 'HKLM:\SOFTWARE\AI-decanat\Kiosk2D' -ErrorAction SilentlyContinue
if ($state.Provisioned -eq 1) {
    $logon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $logon DefaultUserName 'DecanatKiosk'
    Set-ItemProperty $logon DefaultDomainName $env:COMPUTERNAME
}
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($repair))
    $taskAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $encoded"
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Settings $settings `
        -User 'SYSTEM' -RunLevel Highest | Out-Null
}

if ($Action -eq 'Disable') {
    if (-not $state) { throw 'There is no managed kiosk account.' }
    Set-ItemProperty -LiteralPath $statePath -Name Provisioned -Value 0
    if (Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false
    }
    if ($state.OwnsPassword -eq 1 -or $state.BackedUpPassword -eq 1) {
        Restore-Logon ($state.OwnsPassword -eq 1) ($state.BackedUpPassword -eq 1)
        Set-ItemProperty -LiteralPath $statePath -Name OwnsPassword -Value 0
        Set-ItemProperty -LiteralPath $statePath -Name BackedUpPassword -Value 0
    }
    Disable-LocalUser -SID $user.SID
    $profile = Get-ProfilePath $state.Sid
    if ($profile) { Set-KioskShell $profile $state.Sid '' }
    if (Test-Path -LiteralPath $state.Installation) {
        Invoke-Icacls $state.Installation /remove "*$($state.Sid)"
    }
    Write-Output 'Kiosk disabled. Sign out of the kiosk session to finish.'
    return
}

if ($state.Provisioned -eq 1) { throw 'Disable the existing kiosk configuration before replacing it.' }
if ($user -and -not $state) { throw 'A local account already uses the reserved kiosk name.' }
if (Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue) {
    throw 'The kiosk scheduled task name is already in use.'
}
if ($logon.AutoAdminLogon -eq '1' -or $logon.AutoLogonCount -or
    $logon.DefaultUserName -eq $userName -or $logon.PSObject.Properties['DefaultPassword'] -or
    [DecanatKioskNative]::ManagePassword($backupSecret, $null, $true, $null)) {
    throw 'Existing Windows autologon settings must be cleared by the system administrator first.'
}
if (-not $Installation) { throw 'Supply the full path to the installed 2D distribution.' }
$app = Get-Item -LiteralPath $Installation
$Installation = $app.FullName
if (-not $app.PSIsContainer -or $Installation -eq $app.PSDrive.Root -or
    $app.Attributes -band [IO.FileAttributes]::ReparsePoint -or
    $app.PSDrive.DisplayRoot -or ($app.PSDrive.Root -notmatch '^[A-Za-z]:\\$')) {
    throw 'Use a dedicated local installation directory.'
}
$exe = Join-Path $Installation 'Decanat.exe'
$manifest = Get-Content -LiteralPath (Join-Path $Installation 'distribution.json') -Raw | ConvertFrom-Json
if ($manifest.presentation -ne '2d' -or -not (Test-Path -LiteralPath $exe -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $Installation 'runtime\python.exe') -PathType Leaf)) {
    throw 'A complete 2D distribution is required.'
}
if (-not (Test-Path -LiteralPath $StaffPasswordHash -PathType Leaf)) {
    throw 'Set the staff password in Decanat 2D before enabling kiosk mode.'
}

$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = [byte[]]::new(32)
try {
    $random.GetBytes($bytes)
    $password = ConvertTo-SecureString ("K7!" + [Convert]::ToBase64String($bytes)) -AsPlainText -Force
} finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
    $random.Dispose()
}
$profile = $null
$aclChanged = $false
$ownsPassword = $false
$backedUpPassword = $false
$secretRestoreNeeded = $false
$taskRegistered = $false
try {
    if (-not $user) {
        $user = New-LocalUser -Name $userName -FullName 'Decanat 2D' -Password $password -Disabled `
            -PasswordNeverExpires -UserMayNotChangePassword
    } else {
        Set-LocalUser -SID $user.SID -Password $password
    }
    $sid = $user.SID.Value
    New-Item -Path $statePath -Force | Out-Null
    Set-ItemProperty -LiteralPath $statePath -Name Sid -Value $sid
    Set-ItemProperty -LiteralPath $statePath -Name Installation -Value $Installation
    New-ItemProperty -LiteralPath $statePath -Name Provisioned -Value 0 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -LiteralPath $statePath -Name OwnsPassword -Value 0 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -LiteralPath $statePath -Name BackedUpPassword -Value 0 -PropertyType DWord -Force | Out-Null
    foreach ($name in $winlogonNames) {
        $value = $logon.PSObject.Properties[$name]
        if ($value) {
            Set-ItemProperty -LiteralPath $statePath -Name "Previous$name" -Value $value.Value
        } else {
            Remove-ItemProperty -LiteralPath $statePath -Name "Previous$name" -ErrorAction SilentlyContinue
        }
    }
    $state = Get-ItemProperty -LiteralPath $statePath
    $usersGroup = Get-LocalGroup -SID 'S-1-5-32-545'
    if (-not (Get-LocalGroupMember -Group $usersGroup | Where-Object SID -eq $user.SID)) {
        Add-LocalGroupMember -Group $usersGroup -Member $user
    }

    $profile = Get-ProfilePath $sid
    if (-not $profile) {
        $profileBuffer = [Text.StringBuilder]::new(260)
        $result = [DecanatKioskNative]::CreateProfile($sid, $userName, $profileBuffer, $profileBuffer.Capacity)
        if ($result -ne 0) { [Runtime.InteropServices.Marshal]::ThrowExceptionForHR($result) }
        $profile = $profileBuffer.ToString()
    }
    Set-KioskShell $profile $sid ('"{0}" --kiosk' -f $exe)
    $data = Join-Path $profile 'AppData\Local\AI-decanat-2D'
    New-Item -ItemType Directory -Path $data -Force | Out-Null
    $kioskHash = Join-Path $data 'admin.hash'
    if (-not (Test-Path -LiteralPath $kioskHash)) {
        Copy-Item -LiteralPath $StaffPasswordHash -Destination $kioskHash
    }

    $aclChanged = $true
    Invoke-Icacls $Installation /grant "*${sid}:(OI)(CI)RX"
    Invoke-Icacls $Installation /deny "*${sid}:(OI)(CI)(WD,AD,WEA,WA,DE,DC,WDAC,WO)"
    Register-KioskLogonTask
    $taskRegistered = $true
    Enable-LocalUser -SID $user.SID
    $secretRestoreNeeded = $true
    $backedUpPassword = [DecanatKioskNative]::ManagePassword($logonSecret, $null, $true, $backupSecret)
    if ($backedUpPassword) { Set-ItemProperty -LiteralPath $statePath -Name BackedUpPassword -Value 1 }
    [void][DecanatKioskNative]::ManagePassword($logonSecret, $password, $false, $null)
    $ownsPassword = $true
    Set-ItemProperty -LiteralPath $statePath -Name OwnsPassword -Value 1
    Set-ItemProperty -LiteralPath $winlogon -Name DefaultUserName -Value $userName
    Set-ItemProperty -LiteralPath $winlogon -Name DefaultDomainName -Value $env:COMPUTERNAME
    Set-ItemProperty -LiteralPath $winlogon -Name AutoAdminLogon -Value '1'
    Set-ItemProperty -LiteralPath $statePath -Name Provisioned -Value 1
} catch {
    $failure = $_
    if ($secretRestoreNeeded) { Restore-Logon $ownsPassword $backedUpPassword }
    if ($user) { Disable-LocalUser -SID $user.SID }
    if ($taskRegistered) { Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false }
    if ($profile) { Set-KioskShell $profile $sid '' }
    if ($aclChanged) { Invoke-Icacls $Installation /remove "*$sid" }
    if ($state) {
        Set-ItemProperty -LiteralPath $statePath -Name OwnsPassword -Value 0
        Set-ItemProperty -LiteralPath $statePath -Name BackedUpPassword -Value 0
    }
    throw $failure
} finally {
    $password.Dispose()
}
Write-Output 'Kiosk configured. Reboot to sign in to Decanat 2D automatically.'
