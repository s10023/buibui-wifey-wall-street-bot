<#
.SYNOPSIS
    Register wifey's scheduled jobs with Windows Task Scheduler.

.DESCRIPTION
    The Windows half of `deploy/systemd/user/*.timer`. One row in $Jobs per unit, so the
    two can be diffed by eye.

    Nothing installs these automatically, on either host -- registering signal-watch
    means Telegram starts going out, including to the wife channel, so it stays an
    operator decision.

    Run from an ELEVATED shell: `LogonType S4U` (the `loginctl enable-linger` equivalent,
    which lets a task run when the user is not logged in) cannot be registered otherwise.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/windows/install-tasks.ps1
    powershell -ExecutionPolicy Bypass -File deploy/windows/install-tasks.ps1 -WhatIf
    powershell -ExecutionPolicy Bypass -File deploy/windows/install-tasks.ps1 -Only wifey-signal-watch

    `powershell` is Windows PowerShell 5.1, which ships with Windows; `pwsh` (7.x) is a
    separate install and absent on the personal laptop.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string[]] $Only,
    [string]   $TaskPath = '\wifey\',
    [string]   $RepoRoot,
    [string]   $BashExe  = 'C:\Program Files\Git\bin\bash.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Resolved here, not as a param() default: Windows PowerShell 5.1 leaves $PSScriptRoot
# empty while evaluating param() defaults under `-File`, so a default built from it
# fails with "Cannot bind argument to parameter 'Path' because it is an empty string".
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..')).Path
}

# ---------------------------------------------------------------------------------
# The jobs. Mirrors `deploy/systemd/user/`, and the UTC times are copied from the
# `OnCalendar=` lines verbatim.
#
# Do not port the parent's schedules. `buibui-signal-watch.timer` fires
# `OnCalendar=*:01/15`, i.e. every 15 minutes against a 24h crypto tape.
# `wifey-signal-watch.timer` carries a comment saying not to port the parent's
# `OnCalendar=*:01/15`, for this reason: wifey has one RTH session and fires once a day,
# pre-open. A 15-minute cadence here would re-scan the same forming bar ~26 times a day
# and dispatch against a watermark that has not moved.
#
# Because nothing here repeats sub-daily, no job gets a repetition element at all --
# which sidesteps the parent's worst registration bug outright. Recorded so the next
# person does not rediscover it: `RepetitionDuration = [TimeSpan]::Zero` is rejected by
# `Register-ScheduledTask` ("(8,26):Duration:PT0S"), and so is `MaxValue`. Indefinite
# repetition is an EMPTY <Duration>, obtainable only by building a trigger with a real
# duration and then blanking it. A literal `P1D` registers cleanly and then silently
# stops repeating after a day.
# ---------------------------------------------------------------------------------
$Jobs = @(
    @{
        Name    = 'wifey-signal-watch'
        # OnCalendar=Mon..Fri *-*-* 08:30:00 UTC
        Utc     = '08:30'
        Days    = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'
        Command = 'CATCH_UP=1 make go-live'
        # TimeoutStartSec=900
        Timeout = [TimeSpan]::FromMinutes(15)
    },
    @{
        Name    = 'wifey-backup'
        # Two OnCalendar= lines -> two triggers on one task.
        Utc     = '08:10', '13:10'
        Command = 'deploy/backup-analytics.sh --weekly-if-due'
        Timeout = [TimeSpan]::FromMinutes(15)
    },
    @{
        Name    = 'wifey-backup-offsite'
        # OnCalendar=*-*-* 13:55:00 UTC
        Utc     = '13:55'
        Command = 'deploy/backup-offsite.sh'
        # TimeoutStartSec=3600
        Timeout = [TimeSpan]::FromHours(1)
        # Registered disabled. `backup-offsite.sh` runs `rclone sync`, which mirrors
        # deletions. Until this host has its own remote pinned to its own
        # root_folder_id, a scheduled run could mirror an empty local tree over the
        # snapshots it is meant to protect. Enable it by hand once the remote is
        # verified with `make backup-offsite-dry-run`.
        Disabled = $true
    },
    @{
        Name    = 'wifey-universe-sync'
        # OnCalendar=Sat *-*-* 10:00:00 UTC
        Utc     = '10:00'
        Days    = , 'Saturday'
        Command = 'make wifey-universe-sync'
        Timeout = [TimeSpan]::FromHours(1)
    },
    @{
        Name    = 'wifey-daily-check'
        # OnCalendar=*-*-* 09:15:00 UTC -- after signal-watch and the 08:10 backup
        Utc     = '09:15'
        Command = 'make session-digest TELEGRAM=1'
        Timeout = [TimeSpan]::FromMinutes(5)
    }
)

function ConvertTo-LocalTrigger {
    <#
        systemd units are declared in UTC; Task Scheduler triggers are LOCAL. Convert
        the instant at install time rather than writing local times into $Jobs, so the
        table above stays diffable against the unit files.

        ⚠ The resulting StartBoundary round-trips with a `Z` suffix, so the anchor really
        is UTC (measured on the parent). Whether a daily RECURRENCE stays UTC-aligned
        across a DST transition is UNTESTED -- irrelevant on MYT (UTC+8, no DST), but
        verify it before trusting this elsewhere.
    #>
    param([string] $Utc, [string[]] $Days)

    $parts = $Utc.Split(':')
    $today = [datetime]::UtcNow.Date
    $utcAt = [datetime]::new(
        $today.Year, $today.Month, $today.Day,
        [int]$parts[0], [int]$parts[1], 0, [DateTimeKind]::Utc
    )
    $at = $utcAt.ToLocalTime()

    if ($Days) {
        return New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $at
    }
    return New-ScheduledTaskTrigger -Daily -At $at
}

foreach ($job in $Jobs) {
    if ($Only -and $job.Name -notin $Only) { continue }

    $command = '{0} -- {1}' -f $job.Name, $job.Command
    $inner = "cd '$RepoRoot' && deploy/windows/job.sh $command"
    $action = New-ScheduledTaskAction -Execute $BashExe -Argument "-lc `"$inner`"" -WorkingDirectory $RepoRoot

    $triggers = @($job.Utc | ForEach-Object {
            ConvertTo-LocalTrigger -Utc $_ -Days $(if ($job.ContainsKey('Days')) { $job.Days } else { $null })
        })

    # Four defaults kill a laptop job silently. Every one is set explicitly
    # because the default is wrong here, and none of them announces itself -- the task
    # simply does not run, or stops mid-flight, and `LastTaskResult` reports the stop as
    # though it were the job's own verdict.
    #
    #   DisallowStartIfOnBatteries  stops the moment the charger is out
    #   StopIfGoingOnBatteries      kills a running scan mid-flight
    #   StopOnIdleEnd               stops when you touch the laptop
    #   ExecutionTimeLimit          defaults to 72h, so one hung run blocks its
    #                               successor for three days
    #
    # StartWhenAvailable is the `Persistent=true` equivalent: it runs a missed job once
    # the machine is back, which is what makes a laptop schedule usable at all.
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -ExecutionTimeLimit $job.Timeout `
        -MultipleInstances IgnoreNew

    # `loginctl enable-linger`'s equivalent: run whether or not the user is logged on,
    # without storing a password. Needs an elevated shell to register.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

    if ($PSCmdlet.ShouldProcess("$TaskPath$($job.Name)", 'Register-ScheduledTask')) {
        Register-ScheduledTask -TaskPath $TaskPath -TaskName $job.Name `
            -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
            -Description "wifey: $($job.Command)" -Force | Out-Null

        if ($job.ContainsKey('Disabled') -and $job.Disabled) {
            Disable-ScheduledTask -TaskPath $TaskPath -TaskName $job.Name | Out-Null
            Write-Host "  registered DISABLED  $($job.Name)" -ForegroundColor Yellow
        }
        else {
            Write-Host "  registered           $($job.Name)" -ForegroundColor Green
        }

        # Read the registered XML back. The in-memory trigger object accepts values
        # `Register-ScheduledTask` rejects and prints them back happily, and `-WhatIf`
        # skips the call that does the validating -- so inspecting the object proves
        # nothing. Only a read-back is evidence.
        $xml = Export-ScheduledTask -TaskPath $TaskPath -TaskName $job.Name
        if ($xml -notmatch '<StartBoundary>') {
            throw "$($job.Name): registered task has no StartBoundary - trigger did not survive"
        }
    }
}

Write-Host ''
Write-Host 'Verify with:' -ForegroundColor Cyan
Write-Host "  Get-ScheduledTask -TaskPath '$TaskPath' | Format-Table TaskName,State"
Write-Host "  Get-ScheduledTaskInfo -TaskPath '$TaskPath' -TaskName wifey-signal-watch"
Write-Host ''
# Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so every Write-Host string here
# stays ASCII. Comments may carry non-ASCII; OUTPUT may not, or the operator reads
# mojibake at exactly the moment they are being warned about something.
Write-Host '!! LastTaskResult carries SCHED_S_* status, not exit codes:' -ForegroundColor Yellow
Write-Host '    267011 = never run   267009 = currently running'
Write-Host '  Both report a populated, recent LastRunTime, so a stamp-only check reads a'
Write-Host '  job that has NEVER FIRED as fresh. A never-run task reports 11/30/1999 -'
Write-Host '  a sentinel, not a real time.'
