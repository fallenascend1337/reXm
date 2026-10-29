"""Check the NC button remap and profile apply on the headset, then restore. Close reXm first."""
import time

from rexm import Api

api = Api()
link = api._link
link.start()
for _ in range(40):
    if link.state.get("connected") and "nc_button" in link.state:
        break
    time.sleep(0.5)
else:
    raise SystemExit("not ready: " + str(link.state.get("error")))
time.sleep(1)
orig = dict(link.state)
keys = ("mode", "voice", "level", "chat", "dsee", "quality", "voice_prompts", "eq_preset", "eq_bass", "eq_bands", "nc_button")
print("start:", {k: orig.get(k) for k in keys})


def settle():
    time.sleep(1.5)
    api.refresh()
    time.sleep(5)


alt = "nc_amb_off" if orig["nc_button"] != "nc_amb_off" else "nc_amb"
api.set_nc_button(alt)
settle()
print(("OK " if link.state["nc_button"] == alt else "BAD") + f" nc_button -> {link.state['nc_button']}")
api.set_nc_button(orig["nc_button"])

api.apply_profile("Guests")
settle()
s = link.state
ok = s["mode"] == "ambient" and s["voice"] and s["level"] == 20 and s["chat"]
print(("OK " if ok else "BAD") + f" Guests -> mode={s['mode']} voice={s['voice']} level={s['level']} chat={s['chat']}")

# restore everything the profile touched; the level only sticks while in ambient mode, so set it there first
api.set_mode("ambient", orig["voice"], orig["level"])
time.sleep(1)
api.set_mode(orig["mode"], orig["voice"], orig["level"])
api.set_toggle("chat", orig["chat"])
settle()
end = link.state
diff = {k: (orig.get(k), end.get(k)) for k in keys if orig.get(k) != end.get(k)}
print("restored cleanly" if not diff else f"NOT restored: {diff}")
