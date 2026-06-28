"""Pure aggregator: WS broadcasts + supervisor signals -> health-strip fields."""


class HealthModel:
    def __init__(self):
        self.screen = None
        self.character = None
        self.costume = None
        self.kart = None
        self.fps = None
        self.controller = False
        self.mac = ""
        self.last_clip_t = None

    def apply(self, msg, now=0.0):
        t = msg.get("type")
        if t == "heartbeat":
            self.fps = msg.get("fps")
            self.screen = msg.get("screen") or self.screen
        elif t == "screen_change":
            self.screen = msg.get("to") or self.screen
        elif t == "selection_update":
            self.character = msg.get("character")
            self.costume = msg.get("costume")
            self.kart = msg.get("kart")
        elif t == "clip_done":
            self.last_clip_t = now

    def set_controller(self, connected, mac=""):
        self.controller = bool(connected)
        self.mac = mac or ""

    def snapshot(self, now):
        age = (now - self.last_clip_t) if self.last_clip_t is not None else None
        return {"screen": self.screen, "character": self.character,
                "costume": self.costume, "kart": self.kart, "fps": self.fps,
                "controller": self.controller, "mac": self.mac,
                "last_clip_age": age}
