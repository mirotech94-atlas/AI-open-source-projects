# Guardian product decisions

All 60 questionnaire decisions are settled. Q1–Q5 were answered explicitly; the recommended answers for Q6–Q60 were accepted together. These decisions record product direction; they do not indicate that the changes have been implemented.

## Confirmed answers

**Q1. Who should the next round serve?**  
**Accepted decision:** the owner, nontechnical customers, and technicians managing multiple customers.

**Q2. Which dimensions should improve?**  
**Accepted decision:** logic and clarity, workflow speed, visual polish, and mobile usability.

**Q3. How should multi-sensor jobs be assigned?**  
**Accepted decision:** automatically select a suitable sensor for the target network.

**Q4. Should each audience start with a different default view?**

**Accepted decision:** Different defaults, with an easy view switch: customer decisions, owner health/actions, and technician queues/fleet status, using shared underlying data.

**Q5. Should layout preferences follow the person or the organization?**

**Accepted decision:** Personal layout preferences, including density and saved views. Monitoring and repair policies remain organization settings.

**Q6. What should someone understand within ten seconds of opening Guardian?**  
**Accepted decision:** whether coverage is working, what needs attention, and what Guardian has already verified as handled. Keep the score as supporting context.

**Q7. If no suitable sensor can reach a target, should the job wait or fail immediately?**  
**Accepted decision:** wait with a plain explanation, an expiry time, and a notification if the wait exceeds the expected threshold. Relates to F1.

**Q8. Should another qualified sensor take over abandoned work?**  
**Accepted decision:** automatically for read-only checks; reconcile the previous attempt before retrying a repair that might already have run. Relates to F1/F7.

**Q9. Should a single silent sensor make the whole organization look offline?**  
**Accepted decision:** show partial coverage and name the affected networks and assets; reserve fully offline for loss of all relevant coverage. Relates to F6.

**Q10. What should “online” mean for sleeping phones, cameras, and servers?**  
**Accepted decision:** use device-appropriate states, with a visible last observation. A camera that should always answer needs different treatment from a sleeping phone. Relates to F3/D1.

**Q11. When a check is old, should the last passing result stay green?**  
**Accepted decision:** retain the historical result but label current status stale/unknown once its expected freshness expires. Relates to D2.

**Q12. Should every summary expose its network and time scope?**  
**Accepted decision:** yes, in a short subtitle or detail popover; never require comparing pages to infer the scope. Relates to D1.

**Q13. Should Guardian have an explicit timezone for each site/network?**  
**Accepted decision:** yes, inherited from the organization with per-site overrides; use it for maintenance, reporting, and familiarity. Relates to F10/D7.

**Q14. If data loading fails, should the app keep the last successful values?**  
**Accepted decision:** yes, clearly marked with age and a retry action. First load should show loading or an error, not invented zeros. Relates to D2.

**Q15. Should a reboot be scheduled for an exact time or an allowed window?**  
**Accepted decision:** support both; default to a window with an explicit timezone and a clear missed-window policy. Relates to F2.

**Q16. What should happen if a reboot window is missed?**  
**Accepted decision:** mark it missed and request a new time; do not reboot unexpectedly after the window closes.

**Q17. When should a repair count as successful?**  
**Accepted decision:** distinguish “command completed” from “recovery verified”; use the latter for “Guardian handled it” whenever a follow-up check is available.

**Q18. Should Activity show waiting and execution time separately?**  
**Accepted decision:** yes, alongside the selected sensor, target, progress, and last heartbeat. Relates to F7.

**Q19. What should happen when someone clicks Fix twice?**  
**Accepted decision:** reveal the existing matching active job, and require a deliberate separate action to create another attempt.

**Q20. Should users be able to cancel work?**  
**Accepted decision:** cancel scheduled/waiting work; for running work, show whether safe interruption is supported rather than promising cancellation universally.

**Q21. Should every scan, repair, chat action, and automatic action appear in Activity?**  
**Accepted decision:** yes, with consistent labels and links to full results; maintain the existing protection against duplicating queued repairs in the automatic-fix log. Relates to F8.

**Q22. Should work history connect the initial alert, proposal, job, retry, and verification?**  
**Accepted decision:** yes, as one expandable story with separately identifiable attempts.

**Q23. Should critical findings always rank above warnings?**  
**Accepted decision:** make verified critical findings the first tier, then rank within that tier by impact and evidence; avoid group size alone overriding it. Relates to F11.

**Q24. Should a recommendation and its underlying alert occupy separate top-level decision rows?**  
**Accepted decision:** show one decision with the supporting alert and recommendation nested underneath. Preserve both records for inspection. Relates to D3.

**Q25. Should “90% sure” become evidence-strength wording?**  
**Accepted decision:** yes, unless percentages are calibrated; name the observations and uncertainty. Relates to F11.

**Q26. Should repeated findings say “every scan” only when continuity is measured?**  
**Accepted decision:** yes; otherwise use first seen, last seen, and related-finding counts. Relates to F11.

**Q27. What should Resolve mean while the underlying problem still exists?**  
**Accepted decision:** keep the existing recurrence explanation; distinguish fixed, accepted risk, acknowledged, and temporarily snoozed.

**Q28. Should snoozes end only by time, or also by changed severity/evidence?**  
**Accepted decision:** allow both, with escalation on materially worse evidence so a minor accepted issue does not silently cover a new serious one.

**Q29. When a router fails, should downstream alerts generate separate notifications?**  
**Accepted decision:** group the incident and suppress redundant notifications while retaining affected-device evidence. Guardian already groups causes; extend that behavior only where dependencies are supported. [19]

**Q30. Should business-critical assets have different alert urgency?**  
**Accepted decision:** allow importance and expected availability per asset; a payment service, backup recorder, and visitor's phone should not carry identical consequences.

**Q31. Should a Slack “yes” be tied to the person who requested the proposal?**  
**Accepted decision:** yes by default; optionally allow a named authorized approver, with the actual approver recorded. Keep replies in the channel. Relates to F12.

**Q32. Should pausing Slack also stop incoming commands?**  
**Accepted decision:** expose separate “send notifications” and “accept commands” controls if both behaviors are useful; avoid an ambiguous single paused label. Relates to F12.

**Q33. Should Slack users have individual Guardian roles?**  
**Accepted decision:** yes for customer/technician teams; allow a clearly labeled private owner-channel shortcut. Relates to F12.

**Q34. If several actionable alerts were posted, should “fix it” act on all of them?**  
**Accepted decision:** first show the exact scope unless context names one target; use the current recent-warning/critical filtering and preserve confirmation rules.

**Q35. What should happen to an unfinished proposal after a server restart?**  
**Accepted decision:** retain an auditable proposal with an expiry, then revalidate it before execution; otherwise explicitly explain that it expired.

**Q36. How much technical output should chat show by default?**  
**Accepted decision:** outcome, target, evidence, and next action; expandable command/output details for technicians. Relates to D3.

**Q37. Should familiarity ever automatically trust a device?**  
**Accepted decision:** no; use it to make confirmation easier. Shared names, randomized addresses, and recurring presence are evidence, not identity proof. Relates to F10.

**Q38. Should familiarity differ for homes and businesses?**  
**Accepted decision:** yes; office-hours recurrence and always-on equipment are more useful than “sleeps here” for business networks.

**Q39. Should network presence be called screen time?**  
**Accepted decision:** use “estimated network presence,” with a short explanation of what is and is not measured. Relates to D8.

**Q40. What should Topology show when the whole organization cannot fit readably?**  
**Accepted decision:** readable network summaries first, with click-to-expand and fit-to-selection. Relates to D4.

**Q41. Should inferred connections look different from observed connections?**  
**Accepted decision:** yes, with a legend and evidence in the detail view; animate only relationships supported by suitable recent observations.

**Q42. Should a missing recorder make its cameras “offline” or “unknown”?**  
**Accepted decision:** distinguish recorder unreachable from camera confirmed offline. Show expected, discovered, answering, and unobserved camera counts separately.

**Q43. Should DNS summaries all use the same source/window?**  
**Accepted decision:** yes where they claim comparable totals; otherwise label sensor snapshot versus recorded history and explain coverage. Relates to D6.

**Q44. How should an incorrectly blocked domain be handled?**  
**Accepted decision:** show the matching rule/list and offer a scoped exception with a clear expiry option and verification; keep it distinct from disabling filtering globally.

**Q45. Should Security prioritize failing and unknown checks before passing checks?**  
**Accepted decision:** yes; keep the existing failure-first direction and collapse passing detail with clear target counts. Relates to D5.

**Q46. Should stale evidence reduce certainty without silently changing the historical score?**  
**Accepted decision:** yes; show score and coverage separately so missing checks cannot look like an improvement.

**Q47. When CVEs remain but no packages are pending, what should the app explain?**  
**Accepted decision:** distinguish stale scan results, repository/package availability, excluded packages, and reboot requirements when the evidence supports each explanation; never guess. Relates to D5.

**Q48. Should a manual vulnerability scan offer a before/after comparison?**  
**Accepted decision:** yes, using comparable targets and scan profiles, with new, unchanged, resolved, and unverified findings.

**Q49. Should automatic repair policies be global or per target/action?**  
**Accepted decision:** organization defaults with per-target/action overrides, clearly showing effective policy and inherited settings.

**Q50. Should accepting a security exception require an expiry and a reason?**  
**Accepted decision:** a short reason plus optional expiry by default; make stricter requirements configurable for technician-managed customers.

**Q51. Should dense tables have a comfortable mode?**  
**Accepted decision:** yes; remember the person's choice, while preserving compact defaults for technicians. Relates to D3.

**Q52. Which action should come first on mobile?**  
**Accepted decision:** current decisions and incident details, followed by key health; move long histories and broad tables below.

**Q53. Should narrow tables become cards, or scroll horizontally?**  
**Accepted decision:** use compact summary rows/cards for common decisions; keep horizontal scrolling for genuine comparisons, with the identifying column visible.

**Q54. Should the top response-time strip always occupy space?**  
**Accepted decision:** make it optional and keep site names readable; hide decorative motion before sacrificing labels. Relates to D4.

**Q55. Should status changes announce themselves to keyboard/screen-reader users?**  
**Accepted decision:** announce meaningful completion/errors politely, without stealing focus or announcing every stream event. W3C's status-message technique provides an implementation reference. [20]

**Q56. What accessibility baseline should guide the design pass?**  
**Accepted decision:** use WCAG 2.2 AA as the target and verify contrast, focus visibility, zoom/reflow, target size, and non-color status cues. This review is not a conformance certification. [21]

**Q57. Should reports default differently for customers and technicians?**  
**Accepted decision:** a brief with decisions/outcomes for customers and an evidence-rich operations report for technicians; use the existing preset system. Relates to D7.

**Q58. Should report delivery display actual destination outcomes?**  
**Accepted decision:** yes—queued, sent, failed, suppressed—separate from successfully building or sharing the report. Relates to D7.

**Q59. Should the demo use coherent guided scenarios instead of unrelated generated events?**  
**Accepted decision:** yes, with a persistent demo label and internally consistent counts, timestamps, alerts, and repairs. Relates to D6.

**Q60. What should the public site promise before self-service checkout launches?**  
**Accepted decision:** manual setup with clearly stated trial/pricing terms, a supported-action capability table, and one consistent billing statement. Relates to D8.
