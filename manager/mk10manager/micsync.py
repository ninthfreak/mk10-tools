"""Keeps the PC microphone's mute and Teams' mute the same during a Teams call.

Two mutes on one microphone confuse everyone: Teams shows you live while Windows has the microphone muted, or
the other way round. During a call this keeps them in step:

- When a call starts (or first becomes readable), the PC follows Teams: Teams is what other people see.
- After that, whichever side changed last wins. Mute in Teams and the PC microphone mutes; mute the PC (the
  MK10's key, a keyboard's mic key) and Teams mutes.
- If they still disagree after a follow had time to land, the live side is muted, never the muted side unmuted.
  A failed follow can't open a microphone someone closed. After a few tries with nothing changing it stops
  trying (and stops reporting failures) until something does change.

Outside a call nothing is touched. Pure logic: the controller asks `decide()` and carries out what it says.
"""

PC_MUTE, PC_UNMUTE, TEAMS_MUTE, TEAMS_UNMUTE = "pc_mute", "pc_unmute", "teams_mute", "teams_unmute"


class MicSync:
    PENDING_SECONDS = 3.0      # how long a follow gets to land before the two are compared again
    MAX_TRIES = 3              # follows in a row without either side moving, before giving up for now

    def __init__(self):
        self.prev = None           # (teams muted, pc muted) when the last decision was taken, during a call
        self.pending_until = 0.0
        self.tries = 0

    def reset(self):
        self.prev = None
        self.pending_until = 0.0
        self.tries = 0

    def decide(self, teams, pc_muted, now: float) -> str | None:
        """One of PC_MUTE, PC_UNMUTE, TEAMS_MUTE, TEAMS_UNMUTE, or None when nothing needs doing."""
        if teams is None or not teams.available or not teams.in_call or teams.muted is None or pc_muted is None:
            self.reset()
            return None
        t, pc = bool(teams.muted), bool(pc_muted)
        prev, self.prev = self.prev, (t, pc)
        if t == pc:
            self.pending_until = 0.0
            self.tries = 0
            return None
        if now < self.pending_until:
            self.prev = prev               # still waiting: keep comparing against when the follow was asked for
            return None
        if prev is not None and (t != prev[0]) != (pc != prev[1]):
            self.tries = 0                                # one side moved: that's news, act on it afresh
        if self.tries >= self.MAX_TRIES:
            return None
        if prev is None or (t != prev[0] and pc == prev[1]):
            action = PC_MUTE if t else PC_UNMUTE          # the call started, or Teams changed: the PC follows
        elif pc != prev[1] and t == prev[0]:
            action = TEAMS_MUTE if pc else TEAMS_UNMUTE   # the PC changed: Teams follows
        else:
            action = PC_MUTE if t else TEAMS_MUTE         # stuck different: mute whichever side is live
        self.tries += 1
        self.pending_until = now + self.PENDING_SECONDS
        return action
