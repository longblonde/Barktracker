# v0.15 refactor: open questions

Answer inline (or just by number) and they'll go into the second commit on this branch. This file is
removed before the branch merges into main.

## Decisions I made that you may want to change

1. **Detection levels live only on Tuning.** The loudness trigger, the "ignore quieter than" level and the
   bark confidence are set on Detection and tuning (with the live what-if view). Settings → Microphone
   and detection shows their current values and links there. Want them editable in Settings too?
2. **Opening a clip counts as reviewing it** (it becomes "Manual"), as before. Would you rather have an
   explicit "Looks right" button, so opening a clip just to listen doesn't change its tag?
3. **What counts as doubt** (the "Unreviewed" tag): bark score under 60%, an automatic dog match under
   75%, or any bark it couldn't name (when you have dogs). Adjust any of these?
4. **The status filter on Clips** (Unreviewed / Auto / Manual) only filters the 30 clips on the current
   page. I can make it filter the whole list server-side; it's a moderate change.
5. **Households are still "dogs with the same address"**, with a rename/owner button per household.
   Want a proper household record instead (its own name, owner, address and color, dogs picked from a
   list), so the address can stay off the screen?
6. **Dogs you haven't named:** naming one labels only the 5 example barks; the rest follow when the
   Pi re-matches. Label the whole group in one go instead (faster, but a wrong group mislabels more)?
7. **Quiet hours are one window for every day.** Many cities have later weekend hours. Want separate
   weekday and weekend windows?
8. **The dashboard bearing chart** opens on "This day". Default to 7 days instead?
9. **Direction counts in dog matching** whenever two microphones are in use (switch on the Dogs page and
   in Settings). OK as the default?

## Ideas, if you want them

10. **Theme packs:** themes are colors and behaviors only. Should a theme also carry the live band's nap /
    sit / bark pictures (a zip with the JSON and the images)?
11. **Alerts:** right now an alert goes out when a clip ends. Also useful: "still barking after N
    minutes" while it's happening, and a daily or weekly summary message. Want either?
12. **Full-screen live view** (click the live band picture): add the bearing chart there when two
    microphones are in use?
13. **Bark by bark:** the Dogs page now shows each dog's rhythm (pauses, bursts, barks a minute). Want a
    per-dog timeline too (every bark of one dog over a day, like the dashboard strip but per bark)?
14. **Merging:** squash this branch into one commit on main, or keep the commits?

## Switching between this branch and main

Settings saved on this branch use the new names (`direction.*` instead of `dev.array*`). If you go back
to main, the direction test will be off there until you tick it again; your log, labels and clips are
unaffected either way.
