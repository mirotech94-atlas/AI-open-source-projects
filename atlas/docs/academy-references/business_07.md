# Study note: business_07

Topic searched: "n8n form trigger automation tutorial" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- Form Submission Trigger in n8n Explained | Automate & Scale Your Business with AI -- https://www.youtube.com/watch?v=eBT7bENinhQ -- Kyle Dickson, 16,310 views, 18:35, read 2026-11-25. 178 words/min; first on-screen action at 237 s (transcript heuristic); 1 chapters.
- Beginner n8n Tutorial | Feed Data into Google Sheets from n8n Form | Step-by-Step Automation -- https://www.youtube.com/watch?v=MlHyFdEqNpw -- AI UpTrends, 8,774 views, 5:10, read 2026-11-25. 192 words/min; first on-screen action at 65 s (transcript heuristic); 4 chapters.
- n8n Forms Trigger Tutorial for Beginners (Complete Setup Guide) -- https://www.youtube.com/watch?v=1HH9p35wBvI -- Juan Liberato, 3,396 views, 13:59, read 2026-11-25. 119 words/min; first on-screen action at 58 s (transcript heuristic); 0 chapters.

## How they open

Kyle Dickson names the node and promises a tour of every setting, then lists
ideas: a content scheduler, client onboarding, testimonial collection,
rewriting one blog for several channels. AI UpTrends opens with a generic
pitch for the series, then states one goal: put form answers into a Google
sheet. Juan Liberato introduces himself and the trigger, and gives a quick
client-fills-a-form example before opening a blank canvas. None starts from
a finished, working form; all start by talking.

## The order they explain things

- Kyle: test link versus production link, then each setting top to bottom:
  password protection on the page, form title and text, then every field type
  (text, dropdown, email, file, text box, date, checkbox), then the options
  at the bottom (time zone, button label, styling).
- AI UpTrends: add the trigger, build a hotel-booking form with a name, a
  room choice and a required flag, run it, learn there is a second link, then
  an Append Row step into a sheet, connect Google, map the columns by
  dragging, submit a test, see the new row.
- Juan: blank canvas, the two links, sign-in options, fields one by one, the
  options list, restyling with help from an AI, then a multi-page form with
  a closing message.

## Examples and analogies

- A hotel booking form with a name and a room preference (AI UpTrends).
- A contact and lead form with a preferred channel and a consent question
  (Juan).
- A form used as a front door for a content scheduler or a testimonial
  collector (Kyle).
- Asking an AI to change a button's colour by pasting in the style code
  (Juan).

## Beginner mistakes they address

- Opening the test link without first starting the workflow's listening
  step, so the form doesn't appear (Kyle).
- Sharing the test link instead of the production link, and forgetting that
  the production one needs the workflow switched on.
- Leaving the "made with n8n" footer on a customer-facing form.
- Making an email field a plain text field, so any text gets through.
- Typing sheet values by hand: AI UpTrends shows the sheet's columns being
  read in so you drag each form answer onto its column.
- A long form on one page that tires people out (Juan splits it in steps).

## Pacing

Kyle runs about 178 words a minute and does not reach his first on-screen step
until about four minutes in, because the opening list of ideas is long. AI
UpTrends is the shortest, near 192 words a minute, and shows an action at
about a minute. Juan is the slowest, about 119, with a patient, step-by-step
walk through each field. Kyle's and Juan's walk through every
setting drifts from a first-lesson goal, and the second half of Kyle's reads
as a reference page.

## What they do badly, and what we do better

- Two of them tour every setting before the form does anything. We build
  three fields, run it, and then connect it to the next step.
- Nobody says what a feedback form is for. We tie it to one job: a customer
  leaves feedback and you reply.
- The test and production links are mentioned but not tied to a rule. We say
  one thing plainly: test while building, share only the production link.
- Kyle never connects the form to anything beyond the trigger; AI UpTrends
  stops at a sheet row. We carry the answer on to a reply you approve.
- Styling and HTML formatting take a lot of time in two videos and do not
  help a beginner finish.

## What we take (structure only)

1. A tiny form, three fields, built first.
2. The two links, with the rule on which one is for customers.
3. Mark the email field as an email, so mistakes are caught early.
4. Test by submitting it yourself, twice, and look at what arrives.
5. Leave styling and multi-page forms to a later lesson.
