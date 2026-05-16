# Run this script once to set up the 4x daily IPO Tracker schedule
# Right-click > "Run with PowerShell" or run from an admin PowerShell prompt

$python = "C:\Users\Mobin\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$script = "C:\Users\Mobin\IPO_Tracker\ipo_tracker.py"
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`""

$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At "08:00AM"),
    (New-ScheduledTaskTrigger -Daily -At "12:00PM"),
    (New-ScheduledTaskTrigger -Daily -At "04:00PM"),
    (New-ScheduledTaskTrigger -Daily -At "08:00PM")
)

$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName   "IPO Tracker" `
    -Action     $action `
    -Trigger    $triggers `
    -Settings   $settings `
    -Description "Checks SEC EDGAR 4x daily for IPO filings and sends Telegram alerts" `
    -Force

Write-Host ""
Write-Host "✅ IPO Tracker scheduled successfully!" -ForegroundColor Green
Write-Host "   Runs at: 8:00 AM | 12:00 PM | 4:00 PM | 8:00 PM" -ForegroundColor Cyan
Write-Host ""
Write-Host "To verify, open Task Scheduler and look for 'IPO Tracker'" -ForegroundColor Yellow
