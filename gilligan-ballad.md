# The Ballad of tweakcc-gilligan

This is a parody of the Gilligan's Island theme song (the season 2 version). The project author
posted it to r/ClaudeCode. The style is intentional. The verses are written in Claude speak, with
deliberate AI-slop phrases. That slop is what the stock prompt produces. One goal of patching the
binary is to improve the writing quality of Claude Code.

Original post: https://www.reddit.com/r/ClaudeCode/comments/1vbrrm2/just_sit_right_back_and_youll_hear_a_tale_the/

```text
Just sit right back and you'll hear a tale,
a tale of a fateful patch
That started from a stock install
aboard one npm batch.

The mate was a mighty prompt-rewrite,
the skipper regex-sure.
They set their sails for Windows shores
on a three-hour chore. A three-hour chore.

The errors started getting rough,
the ReferenceError tossed.
If not for the guard that refused to repack,
the binary would be lost. The binary would be lost.

The build set ground on the shore of this
uncharted desktop isle:
with unnerfcc, the tweakcc too,
the npm and its shim,
the reminder files,
the common version and the reset,
here on Claude Code's Isle.
```

---

And now, tonight's episode: "The Patchman Cometh."

Three open-source repos do the work. unnerfcc [1] rewrites the prompt strings inside the binary and
lifts the reasoning-effort cap. tweakcc-fixed [2] patches code features and binds the
system-reminder overrides from `~/.tweakcc/system-reminders/`. lobotomized-claude-code [3] supplies
that reminder override set.

The order matters: reset to stock, apply tweakcc-fixed, apply unnerfcc, verify. Reversed,
tweakcc-fixed cannot match its patches and refuses to repack, so the binary stays untouched. Every
Claude Code session must be closed for the apply, because a running `claude.exe` locks the binary.
The chain works on Windows because the repack library was ported to the Windows PE format [4].

The repository ships the skill that drives the chain. `skills/tweakcc-update/` holds `SKILL.md`,
`install.py --prepare`, `install.py --apply`, and `verify.py`. Install the skill into
`.claude/skills/`. Claude Code then loads it on the words "tweakcc", "unnerfcc", "patch Claude
Code", or "tweakcc-gilligan", and guides you through prepare, apply, and verify. The current
procedure is in [skills/tweakcc-update/SKILL.md](skills/tweakcc-update/SKILL.md) and in the Getting
started section of the [README](README.md). To automate this entire sequence in one step on Windows
and Unix, see tweakcc-gilligan [5].

Tune in next update, same slop time, same slop channel. Rescue arrives when the defaults ship un-nerfed upstream; until then, reruns air whenever both catalogs cover a new release.

---

```text
So this is the tale of the un-nerfed build,
it's patched for a long, long time.
It ported the repack to Windows PE,
and that was an uphill climb.

The prompt tool and the patcher too
will do their very best
to keep the model thorough-grade
in its little binary nest.

No guessing, no slop, no hand-rolled scripts,
not a single luxury.
Like a senior engineer,
as rigorous as can be.

So run the chain again, my friends,
when npm ships a new file,
but check the common version first,
here on Claude Code's Isle!
```

## References

[1] lukehutch, unnerfcc: https://github.com/lukehutch/unnerfcc

[2] skrabe, tweakcc-fixed: https://github.com/skrabe/tweakcc-fixed

[3] skrabe, lobotomized-claude-code: https://github.com/skrabe/lobotomized-claude-code

[4] brooksbUWO, Windows PE binary repack (Bun container), pull request 1 on lukehutch/unnerfcc: https://github.com/lukehutch/unnerfcc/pull/1

[5] brooksbUWO, tweakcc-gilligan: https://github.com/brooksbUWO/tweakcc-gilligan
