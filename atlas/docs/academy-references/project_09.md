# Study note: project_09

Topic searched: "n8n backup workflows update docker" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=6UKMjyQozLo -- "How to Update n8n on Docker Desktop (No Data Loss!)", REALIQ, 7,297 views, 4:34, read 2026-11-25. 134 words/min; first on-screen action at 11 s (transcript heuristic); 6 chapters.
- https://www.youtube.com/watch?v=QP6kWRwHttI -- "How To Backup N8n Workflows in 10 Minutes FAST!", Solomon Christ AI + Automation Mastery, 6,455 views, 13:37, read 2026-11-25. 227 words/min; first on-screen action at 117 s (transcript heuristic); 11 chapters.
- https://www.youtube.com/watch?v=oWIg1CQYyd0 -- "How to Update n8n on Docker Desktop | No Data Loss! Full Guide", Phylux Express, 5,773 views, 6:37, read 2026-11-25. 147 words/min; first on-screen action at 36 s (transcript heuristic); 4 chapters.

## How they open

Both update videos open on the same promise, a newer n8n with your work
intact, and are at the Docker screen within about a minute. REALIQ starts
with the running editor, then a stop. Phylux Express says up front that it
will back up first and shows the old version is several releases behind.
Solomon Christ opens on the finished product, a folder of dated workflow
files in Google Drive, and promises it takes ten minutes. Honest note: his
video is about copying workflows out through the n8n API, which is a
different job from backing up an instance.

## The order they explain things

- REALIQ: stop the container, remember the address, delete the container
  and image, pull the newest image, run it again with the same port, the
  same data folder, the same container path and the same environment
  values, then log in and see workflows are still there.
- Phylux Express: stop n8n, find the data folder through the container's
  bind mounts, copy it somewhere safe, delete container and image, pull the
  latest, run it pointing at the same folder, create the owner login again,
  check that the update count dropped to zero.
- Solomon Christ: make a dated folder in the drive, create an API key in
  n8n's settings and a credential from it, list every workflow, loop over
  them, turn each into a text file named after the workflow, upload each to
  the folder, run it, then for advanced users copy the compose and custom
  Docker files off the server with an SFTP client.

## Examples and analogies

- Delete the container, keep the folder: the program is replaceable and the
  folder on the host is the real thing (both update videos). A clean version
  of the idea that the volume is where the work lives.
- A pre-update copy of the data folder as a safety net, so a mistake costs
  nothing (Phylux Express).
- A dated folder name per backup so versions never overwrite each other
  (Solomon Christ).
- Open one exported file and paste it into a blank canvas to prove the
  backup restores (Solomon Christ). A small, convincing test.

## Beginner mistakes they address

- Deleting the container and thinking the data is gone; both update videos
  stress that it lives outside.
- Pointing the new container at a new empty folder instead of the old one.
- Changing the port, folder or environment values on the rerun, so the new
  container behaves differently.
- Forgetting the files that are not in the image, such as the compose file
  and custom Docker files (Solomon Christ).
- Having no copy off the machine, and not trying a restore.
- Leaving an API key with no expiry (he does this himself, and does not
  warn about it).

## Pacing

Both update videos are slow and plain, about 134 and 147 words a minute,
with long waits for downloads that they narrate. Solomon Christ is much
faster, about 227, and drifts into client stories and a community pitch in
the middle. Our script should keep the update videos' slow, one-step-at-a-
time rhythm and skip the waiting.

## What they do badly, and what we do better

- Both update videos pull the newest image, with no tag. We pin a
  version, say to read the release notes and to test on a copy first.
- Neither update video backs up the encryption key by name, and one skips
  a backup entirely. We make the key and the data folder the first thing
  saved, and say a restore without the key leaves credentials unreadable.
- The workflow-export video shows its API key on screen and exports no
  credentials at all, so it is not a full backup. We name what a command
  backup leaves out: users, run history and settings.
- It also never says exported files can carry credential names and IDs. We
  show how to share a file without secrets.
- Two of three click through a desktop app. We give the command line, which
  can be repeated and put on a schedule.

## What we take (structure only)

1. Open on the question what would you lose if the server vanished today.
2. Backup first: the data folder and the key, then the CLI exports.
3. Prove it: try a restore, as Solomon Christ does with a pasted file.
4. Update on purpose: stop, back up, change the tag, start with the same
   folder and settings, check the version.
5. Keep what is not in the image: compose and environment files.
6. Pause, quiz, recap.
