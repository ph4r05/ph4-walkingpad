from ph4_walkingpad.utils import defval


def calories_bmr(weight: float, height: float, age: float, male=True) -> float:
    """http://www.shapesense.com/fitness-exercise/calculators/net-versus-gross-calorie-burn-conversion-calculator.shtml"""
    if male:
        return 13.75 * weight + 5 * (height * 100) - 6.76 * age + 66
    else:
        return 9.56 * weight + 1.85 * (height * 100) - 4.68 * age + 655


def calories_rmrcb_minute(weight: float, height: float, age: float, male=True) -> float:
    """http://www.shapesense.com/fitness-exercise/calculators/net-versus-gross-calorie-burn-conversion-calculator.shtml
    per minute"""
    return 1 / 60 * (calories_bmr(weight, height, age, male) * 1.1 / 24)


def calories_walk_minute(speed: float, weight: float, height: float):
    """Cal per minute = (0.035  X body weight in kg) + ((Velocity in m/s ^ 2) / Height in m))
    X (0.029) X (body weight in kg)"""
    vel = speed * 1000 / 3600  # to kmph
    return (0.035 * weight) + ((vel * vel) / height) * (0.029 * weight)


def calories_walk2_minute(speed: float, weight: float, deg: float):
    """
    http://www.shapesense.com/fitness-exercise/calculators/walking-calorie-burn-calculator.shtml
    Valid only for speeds in range 1 - 7.5 kmph
    deg: elevation, 5% elev = 0.05
    """
    speed_matrix = [
        [0.0251, -0.2157, +0.7888, +1.2957],
        [0.0244, -0.2079, +0.8053, +1.3281],
        [0.0237, -0.2000, +0.8217, +1.3605],
        [0.0230, -0.1922, +0.8382, +1.3929],
        [0.0222, -0.1844, +0.8546, +1.4253],
        [0.0215, -0.1765, +0.8710, +1.4577],
        [0.0171, -0.1062, +0.6080, +1.8600],
        [0.0184, -0.1134, +0.6566, +1.9200],
        [0.0196, -0.1205, +0.7053, +1.9800],
        [0.0208, -0.1277, +0.7539, +2.0400],
        [0.0221, -0.1349, +0.8025, +2.1000],
    ]
    if deg <= 0.06:
        matrix_idx = min(len(speed_matrix) - 1, int(round(int(max(-5, deg * 100.0)) + 5.0)))
        return 1 / 60.0 * sum([speed_matrix[matrix_idx][i] * (speed ** (3 - i)) for i in range(4)]) * weight

    mpm = speed * 1000 / 60
    return 1 / 60.0 * ((0.1 * mpm + 1.8 * mpm * deg + 3.5) * weight * 60 * 5 / 1000)


class Profile:
    def __init__(
        self,
        pid=None,
        male=True,
        age=0,
        weight=0,
        height=0,
        token=None,
        did=None,
        email=None,
        password=None,
        password_md5=None,
    ):
        self.pid = pid
        self.male = male
        self.age = age
        self.weight = weight
        self.height = height
        self.token = token
        self.did = did
        self.email = email
        self.password = password
        self.password_md5 = password_md5

    def load_from(self, js):
        self.pid = defval(js, "id")
        self.male = defval(js, "male")
        self.age = defval(js, "age")
        self.weight = defval(js, "weight")
        self.height = defval(js, "height")
        self.token = defval(js, "token")
        self.did = defval(js, "did")
        self.email = defval(js, "email")
        self.password = defval(js, "password")
        self.password_md5 = defval(js, "password_md5")

    def dump(self):
        js = {
            "id": self.pid,
            "male": self.male,
            "age": self.age,
            "weight": self.weight,
            "height": self.height,
            "token": self.token,
            "did": self.did,
            "email": self.email,
            "password": self.password,
            "password_md5": self.password_md5,
        }
        return js

    @staticmethod
    def from_data(data):
        m = Profile()
        m.load_from(data)
        return m

    def __str__(self):
        return "Profile(pid=%s, male=%s, age=%s, weight=%s, height=%s)" % (
            self.pid,
            self.male,
            self.age,
            self.weight,
            self.height,
        )
