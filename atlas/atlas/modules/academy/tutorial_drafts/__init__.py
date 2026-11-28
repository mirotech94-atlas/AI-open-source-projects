"""Draft Academy scripts: written ahead, NOT approved, never counted, never posted.

Same module shape as `tutorial_scripts/` (`<series>_<NN>.py`, one `EPISODE`
built with `scripted(...)`, optional `LAB_FILES`), with two differences:

* every module sets `STATUS = "draft/unapproved"`;
* nothing here is discovered. `tutorial_scripts.for_series` reads only the
  `tutorial_scripts` package, so the runway (`tutorials.runway`), the lineup,
  the render and the upload never see these files.

To approve one (Miro only): `git mv` the module into `tutorial_scripts/`,
delete its `STATUS` line, add an `OPENER_TOPICS` line if it is missing, run
the gate in docs/academy-script-writer.md, and let the push session merge it.
Nothing is imported at package import time.
"""
