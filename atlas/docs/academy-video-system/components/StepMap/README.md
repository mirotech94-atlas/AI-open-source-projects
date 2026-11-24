The three- or four-step workflow map that tells the learner where the current action fits; only the active step animates. Also the frame for a glossary card when a concept needs a definition.

**The editor supplies:** the step names (as the app names its nodes), the one-line description under each, which step is active, and the state chips that are true at that moment.

**Behaviour:** the map sits top-left inside `safe-16-9` and keeps the same position for the whole episode. On each orientation beat the active node lifts in with `title-rise` and its connector from the previous step turns `blue`; everything else stays still. Status chips carry a glyph and a word in `success`, `amber` or `error`; never a colour alone.

| Do | Don't |
|---|---|
| Keep it to four nodes; split a longer workflow into chapters | Show the whole 12-node canvas and shrink the labels |
| Return to the real app right after the map reads | Leave the map on screen as a permanent sidebar |
| Mark a step `success` only after it was tested on camera | Pre-fill every step as done to look finished |
