#!/bin/bash
# Speeds up Blu's MacBookPro8,1 (Ventura via OCLP, 5400 rpm HDD, 4 GB). Blu 11-05: "debloat the MacBook and add the
# optimizations". Run as anubis over SSH: ssh macbook 'bash -s' < mac_optimize.sh   (needs passwordless sudo).
# Every change is listed with its undo in ~/mac-optimize-undo.sh. SIP and Gatekeeper are left alone (Blu's).
set -u
U="$HOME/mac-optimize-undo.sh"
echo "#!/bin/bash" > "$U"
step() { echo "== $1"; }
undo() { echo "$1" >> "$U"; }

step "Spotlight indexing off (the HDD stays busy for hours re-indexing)"
sudo -n mdutil -a -i off >/dev/null && undo "sudo mdutil -a -i on"

step "Hibernation off and the 4 GB sleep image removed (saves disk writes and space)"
old=$(pmset -g | awk '/hibernatemode/{print $2}')
sudo -n pmset -a hibernatemode 0 && sudo -n rm -f /var/vm/sleepimage && undo "sudo pmset -a hibernatemode ${old:-3}"

step "Time Machine local snapshots and automatic backups off"
sudo -n tmutil disable 2>/dev/null; undo "sudo tmutil enable"

step "No automatic macOS update downloads (an update can undo OCLP's root patches)"
sudo -n softwareupdate --schedule off >/dev/null 2>&1
sudo -n defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool false
sudo -n defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticallyInstallMacOSUpdates -bool false
undo "sudo softwareupdate --schedule on; sudo defaults delete /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload; sudo defaults delete /Library/Preferences/com.apple.SoftwareUpdate AutomaticallyInstallMacOSUpdates"

step "Analytics and crash report sending off"
sudo -n defaults write "/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist" AutoSubmit -bool false
sudo -n defaults write "/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist" ThirdPartyDataSubmit -bool false
undo "sudo defaults write '/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist' AutoSubmit -bool true"

step "Siri and Siri suggestions off"
defaults write com.apple.assistant.support "Assistant Enabled" -bool false
defaults write com.apple.Siri StatusMenuVisible -bool false
defaults write com.apple.Siri UserHasDeclinedEnable -bool true
defaults write com.apple.lookup.shared LookupSuggestionsDisabled -bool true
undo "defaults write com.apple.assistant.support 'Assistant Enabled' -bool true; defaults delete com.apple.lookup.shared LookupSuggestionsDisabled"

step "Less window and Dock animation (the GPU is a 2011 HD 3000)"
defaults write NSGlobalDomain NSAutomaticWindowAnimationsEnabled -bool false
defaults write NSGlobalDomain NSWindowResizeTime -float 0.001
defaults write com.apple.dock launchanim -bool false
defaults write com.apple.dock expose-animation-duration -float 0.1
defaults write com.apple.dock mineffect -string scale
defaults write com.apple.finder DisableAllAnimations -bool true
undo "defaults delete NSGlobalDomain NSAutomaticWindowAnimationsEnabled; defaults delete NSGlobalDomain NSWindowResizeTime; defaults delete com.apple.dock launchanim; defaults delete com.apple.dock expose-animation-duration; defaults delete com.apple.dock mineffect; defaults delete com.apple.finder DisableAllAnimations"

step "Reduce transparency and motion"
if defaults write com.apple.universalaccess reduceTransparency -bool true 2>/dev/null && defaults write com.apple.universalaccess reduceMotion -bool true 2>/dev/null; then
  undo "defaults write com.apple.universalaccess reduceTransparency -bool false; defaults write com.apple.universalaccess reduceMotion -bool false"
else
  echo "   (macOS refused: turn on System Settings > Accessibility > Display > Reduce transparency and Reduce motion by hand)"
fi

step "Background photo, media and suggestion analysis off for this user"
for a in com.apple.photoanalysisd com.apple.mediaanalysisd com.apple.suggestd com.apple.knowledge-agent com.apple.ScreenTimeAgent com.apple.gamed com.apple.siriknowledged com.apple.assistantd com.apple.parsecd; do
  launchctl disable "gui/$(id -u)/$a" 2>/dev/null && launchctl bootout "gui/$(id -u)/$a" 2>/dev/null
  undo "launchctl enable gui/\$(id -u)/$a"
done

step "Finder: no .DS_Store on network and USB drives"
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true
defaults write com.apple.desktopservices DSDontWriteUSBStores -bool true

step "Notification Center widgets off (11-05: the News widget alone took 9% CPU)"
for w in $(pluginkit -mA -p com.apple.widgetkit-extension 2>/dev/null | awk '{print $1}' | sed 's/(.*//'); do
  pluginkit -e ignore -i "$w" 2>/dev/null && undo "pluginkit -e use -i $w"
done
# The News widget is not a widgetkit-extension on Ventura; NewsToday2 kept coming back until these were off.
for w in com.apple.news.widget com.apple.news.widgetintents com.apple.news.articlenotificationextension com.apple.news.engagementExtension com.apple.news.NewsAudioExtension com.apple.news.tag com.apple.stocks.widget com.apple.weather.widget; do
  pluginkit -e ignore -i "$w" 2>/dev/null && undo "pluginkit -e use -i $w"
done
killall NewsToday2 StocksWidget WeatherWidget NotificationCenter 2>/dev/null

step "More background agents off: app search, tips, news, stocks, weather, photos library, sharing suggestions"
for a in com.apple.corespotlightd com.apple.tipsd com.apple.newsd com.apple.stocks.widget com.apple.weather.menu com.apple.photolibraryd com.apple.cloudphotod com.apple.followupd com.apple.sharingd.suggestions com.apple.heard com.apple.UsageTrackingAgent com.apple.ReportCrash com.apple.familycircled com.apple.homed com.apple.chronod; do
  launchctl disable "gui/$(id -u)/$a" 2>/dev/null && launchctl bootout "gui/$(id -u)/$a" 2>/dev/null
  undo "launchctl enable gui/\$(id -u)/$a"
done

step "Diagnostic tracing and spin dumps off (they write to the slow disk)"
for a in com.apple.tailspind com.apple.spindump com.apple.ReportCrash.Root com.apple.analyticsd; do
  sudo -n launchctl disable "system/$a" 2>/dev/null && sudo -n launchctl bootout "system/$a" 2>/dev/null
  undo "sudo launchctl enable system/$a"
done

step "Plugged in: never sleep, never turn the screen or disk off, no screensaver (Blu 11-05)"
sudo -n pmset -c sleep 0 displaysleep 0 disksleep 0 womp 1 && undo "sudo pmset -c sleep 1 displaysleep 10 disksleep 10"
defaults -currentHost write com.apple.screensaver idleTime -int 0 && undo "defaults -currentHost delete com.apple.screensaver idleTime"

step "Dock: no recent apps, no bounce; Finder: no warning on emptying Trash, no animations"
defaults write com.apple.dock show-recents -bool false
defaults write com.apple.dock no-bouncing -bool true
defaults write com.apple.finder WarnOnEmptyTrash -bool false

killall Dock Finder SystemUIServer 2>/dev/null
chmod +x "$U"
echo "done; undo with $U (a restart applies everything)"
