"""Flip every setting through rexm's Api, check the headset reports the change, then restore it.
Close reXm first (only one app can hold the control channel)."""
import time

from rexm import Api

api = Api()
link = api._link
link.start()

for _ in range(30):
    if link.state.get("connected") and "voice_prompts" in link.state:
        break
    time.sleep(0.5)
else:
    raise SystemExit("headset did not connect: " + str(link.state.get("error")))
time.sleep(1)
orig = dict(link.state)
print("start:", {k: orig.get(k) for k in ("pause_off", "dsee", "quality", "voice_prompts", "power_off", "mode")})


def check(label, key, do, undo, want):
    do()
    time.sleep(1.5)
    api.refresh()
    time.sleep(4)
    got = link.state.get(key)
    print(f"{'OK ' if got == want else 'BAD'} {label}: wanted {want!r}, headset says {got!r}")
    undo()
    time.sleep(1.5)


for key in ("pause_off", "dsee", "quality", "voice_prompts"):
    o = bool(orig[key])
    check(key, key, lambda: api.set_toggle(key, not o), lambda: api.set_toggle(key, o), not o)

o = orig["power_off"]
alt = "never" if o != "never" else "taken_off"
check("power_off", "power_off", lambda: api.set_power_off(alt), lambda: api.set_power_off(o), alt)

o_sens, o_to = orig["chat_sens"], orig["chat_timeout"]
check("chat_timeout", "chat_timeout", lambda: api.set_chat_config(o_sens, 2), lambda: api.set_chat_config(o_sens, o_to), 2)

p = orig["eq_preset"]


def restore_eq():  # a Manual EQ lives in its bands, so re-send them rather than just the preset
    if p == 0xA0:
        api.set_eq_bands(orig["eq_bass"], orig["eq_bands"])
    else:
        api.set_eq_preset(p)


check("eq_preset", "eq_preset", lambda: api.set_eq_preset(0x16), restore_eq, 0x16)
check("eq_bands", "eq_bands", lambda: api.set_eq_bands(0, [1, 2, 3, 4, 5]), restore_eq, [1, 2, 3, 4, 5])

api.refresh()
time.sleep(4)
end = link.state
diff = {k: (orig[k], end.get(k)) for k in orig if orig[k] != end.get(k) and k not in ("battery", "charging")}
print("restored cleanly" if not diff else f"NOT restored: {diff}")
