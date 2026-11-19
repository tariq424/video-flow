@echo off
rem DeepSeek web chat in its own Chrome profile (sign in once by hand; it stays signed in).
rem Separate from the Flow profile (port 9222) and from Video Studio (42020).
rem No remote-debugging port: Google's sign-in silently refuses browsers that have one.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%~dp0profiles\deepseek" --no-first-run "https://chat.deepseek.com/"
