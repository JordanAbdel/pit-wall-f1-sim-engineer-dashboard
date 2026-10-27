-- PitWall launcher: a stay-open app that runs the engineer from the
-- race-engineer folder, so Applications always runs the latest code.
-- Quit (Dock or Cmd-Q) stops it cleanly, which saves the session.
property appDir : "/Users/jordanabdel/Documents/Claude/race-engineer"
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
	set enginePid to do shell script "cd " & quoted form of appDir & "; nohup build-venv/bin/python -u main.py > logs/app-console.log 2>&1 & echo $!"
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
