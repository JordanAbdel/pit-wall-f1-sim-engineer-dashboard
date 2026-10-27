-- PitWall launcher for the downloadable .app: runs the frozen engine
-- bundled in Contents/Resources/PitWall and keeps user state in
-- ~/Library/Application Support/PitWall (never inside the bundle).
-- Quit (Dock or Cmd-Q) stops it cleanly, which saves the session.
property enginePid : ""

on engineAlive()
	if enginePid is "" then return false
	try
		do shell script "kill -0 " & enginePid
		return true
	on error
		return false
	end try
end engineAlive

on run
	set engineDir to (POSIX path of (path to me)) & "Contents/Resources/PitWall"
	set homeDir to (POSIX path of (path to application support folder from user domain)) & "PitWall"
	do shell script "mkdir -p " & quoted form of (homeDir & "/logs")
	set enginePid to do shell script "export PITWALL_HOME=" & quoted form of homeDir & "; cd " & quoted form of engineDir & "; nohup ./PitWall > " & quoted form of (homeDir & "/logs/app-console.log") & " 2>&1 & echo $!"
	delay 3
	if not engineAlive() then
		set msg to do shell script "tail -5 " & quoted form of (homeDir & "/logs/app-console.log") & " 2>/dev/null; true"
		display alert "PitWall couldn't start" message msg & return & return & "If PitWall is already running, quit it first." as critical
		quit
	end if
end run

on reopen
	if engineAlive() then
		open location "http://127.0.0.1:8777/"
	else
		run
	end if
end reopen

on idle
	if not engineAlive() then quit
	return 10
end idle

on quit
	if engineAlive() then
		do shell script "kill -INT " & enginePid & "; for i in $(seq 1 50); do kill -0 " & enginePid & " 2>/dev/null || exit 0; sleep 0.2; done; kill -TERM " & enginePid & " 2>/dev/null; true"
	end if
	continue quit
end quit
