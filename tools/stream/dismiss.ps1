Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [Windows.Automation.AutomationElement]::RootElement
$names = 'Run in Normal Mode', 'Launch Normal', 'Run Normally'
foreach ($p in Get-Process obs64 -ErrorAction SilentlyContinue) {
    $c = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty, $p.Id)
    foreach ($w in $root.FindAll('Children', $c)) {
        if ($w.Current.Name -notmatch 'Crash|Safe Mode|did not') { continue }
        foreach ($b in $w.FindAll('Descendants', [Windows.Automation.Condition]::TrueCondition)) {
            if ($b.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and $names -contains $b.Current.Name) {
                $b.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
                "clicked $($b.Current.Name)"
            }
        }
    }
}
