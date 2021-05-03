# WalkingPad controller

Simple python script that can control KingSmith WalkingPad A1.
Communicates via [Bluetooth LE GATT](https://www.oreilly.com/library/view/getting-started-with/9781491900550/ch04.html).


## Features

- Switch mode: Standby / Manual / Automatic
- Start belt, stop belt
- Change belt speed (0.5 - 6.0), all options work, e.g. 1.2 not originally usable with the native interface (permits only 0.5 step)
- Change preferences of the belt
    - Max speed
    - Start speed
    - start type (intelli)
    - Sensitivity in automatic mode
    - Display
    - Child lock 
    - Units (miles/km)
    - Target (time, distance, calories, steps)
- Ask for current state (speed, time, distance, steps)
- Ask for last stored state in the WalkingPad

## Demo

For the best understanding start jupyter-notebook and take a look at [belt_control.ipynb](belt_control.ipynb)

```bash
# Install jupyter-notebook
pip3 install jupyter

# Start jupyter-notebook in this repository
jupyter-notebook .

# open belt_control.ipynb
```

## Library use-case

The main controller class is `Controller` in [pad.py](ph4_walkingpad/pad.py)


## Controller

Controller enables to control the belt via CLI shell.

Start controller: 
```bash
python -m ph4_walkingpad.main --stats 750 --json-file ~/walking.json
```

The command asks for periodic statistics fetching at 750 ms, storing records to `~/walking.json`.

Output
```
---------------------------------------------------------------------------
    WalkingPad controller

---------------------------------------------------------------------------
$> help

Documented commands (use 'help -v' for verbose/'help <topic>' for details):
===========================================================================
alias      help     py  quit          set        speed   stop       
ask_stats  history  Q   run_pyscript  shell      start   switch_mode
edit       macro    q   run_script    shortcuts  status  tasks    

$> status
WalkingPadCurStatus(dist=0.0, time=0, steps=0, speed=0.0, state=5, mode=2, app_speed=0.06666666666666667, button=2, rest=0000)
$> start
$> speed 30
$> speed 15
$> status
WalkingPadCurStatus(dist=0.01, time=16, steps=18, speed=1.8, state=1, mode=1, app_speed=1.5, button=1, rest=0000)
$> status
WalkingPadCurStatus(dist=0.01, time=17, steps=20, speed=1.5, state=1, mode=1, app_speed=1.5, button=1, rest=0000)
$> speed 30
$> s
$> WalkingPadCurStatus(dist=0.98, time=670, steps=1195, speed=6.0, state=1, mode=1, app_speed=6.0, button=1, rest=0000), cal:  38.73, net:  30.89, total:  73.65, total net:  57.91      
$> stop
$> start
$> speed 30
$> status
```

Due to nature of the BluetoothLE callbacks being executed on the main thread we cannot use readline to read from the console,
so the shell CLI does not support auto-complete, ctrl-r, up-arrow for the last command, etc.
Readline does not have async support at the moment. 

### Profile

If the `-p profile.json` argument is passed, profile of the person is loaded from the file, so the controller can count burned calories.
Units are in a metric system.

```json
{
  "id": "user1",
  "male": true,
  "age": 25,
  "weight": 80,
  "height": 1.80,
  "token": "JWT-token"
}
```

### Stats file

The following arguments enable data collection to a statistic file: 

```
--stats 750 --json-file ~/walking.json
```

In order to guarantee file consistency the format is one JSON record per file, so it is easy to append to a file at any time 
without need to read and rewrite it with each update (helps to prevent a data loss in cause of a crash).

Example:

```
{"time": 554, "dist": 79, "steps": 977, "speed": 60, "app_speed": 180, "belt_state": 1, "controller_button": 0, "manual_mode": 1, "raw": "f8a2013c0100022a00004f0003d1b4000000e3fd", "rec_time": 1615644982.5917802, "pid": "ph4r05", "ccal": 23.343, "ccal_net": 18.616, "ccal_sum": 58.267, "ccal_net_sum": 45.644}
{"time": 554, "dist": 79, "steps": 978, "speed": 60, "app_speed": 180, "belt_state": 1, "controller_button": 0, "manual_mode": 1, "raw": "f8a2013c0100022a00004f0003d2b4000000e4fd", "rec_time": 1615644983.345463, "pid": "ph4r05", "ccal": 23.343, "ccal_net": 18.616, "ccal_sum": 58.267, "ccal_net_sum": 45.644}
{"time": 555, "dist": 79, "steps": 980, "speed": 60, "app_speed": 180, "belt_state": 1, "controller_button": 0, "manual_mode": 1, "raw": "f8a2013c0100022b00004f0003d4b4000000e7fd", "rec_time": 1615644984.0991402, "pid": "ph4r05", "ccal": 23.476, "ccal_net": 18.722, "ccal_sum": 58.4, "ccal_net_sum": 45.749}
{"time": 556, "dist": 79, "steps": 981, "speed": 60, "app_speed": 180, "belt_state": 1, "controller_button": 0, "manual_mode": 1, "raw": "f8a2013c0100022c00004f0003d5b4000000e9fd", "rec_time": 1615644984.864169, "pid": "ph4r05", "ccal": 23.608, "ccal_net": 18.828, "ccal_sum": 58.533, "ccal_net_sum": 45.855}
{"time": 557, "dist": 80, "steps": 982, "speed": 60, "app_speed": 180, "belt_state": 1, "controller_button": 0, "manual_mode": 1, "raw": "f8a2013c0100022d0000500003d6b4000000ecfd", "rec_time": 1615644985.606997, "pid": "ph4r05", "ccal": 23.741, "ccal_net": 18.933, "ccal_sum": 58.665, "ccal_net_sum": 45.961}
```

### Reversing Belt API 

#### Easy way - Android logs
I used the easiest way I found - the original Android application is quite generously logging all 
Bluetooth requests and responses; and network requests and responses (JWT included).

After few trial/error attempts I managed to reverse binary packet protocol format.
See [pad.py](/ph4_walkingpad/pad.py) for protocol internals.

You can query from the belt a status message (app does so each 750 ms, approx). The status contains
speed, distance, steps, and very simple CRC code (sum of the payload). Interestingly, calories are not part of the status 
message and cannot be queried either. 

For obtaining logs just plug Android phone via USB, enable development mode on the phone, enable ADB connection and run:

```bash
adb logcat
```

(Or use AndroidStudio)

You then can see the app communication with the belt in real-time. When using the app, it logs also requests 
so you can figure out how commands for e.g., speed change look like.

### Medium - Bluetooth logs

Should vendor remove the logging from the app and you are unable to find APK in archives with the logging, you can always 
enable Bluetooth logs in the Phone development settings. 

This approach is not that straightforward as from logs as you cannot see belt responses in real-time.
The Bluetooth log can be obtained from the device via `adb` and opened in Wireshark. 

You may need to do own journal with times and commands you issued so you can experiment with the belt
(e.g., change speeds), the commands get logged to the Bluetooth log. Then after the experiment,
download the Bluetooth log and map your log entries to the packets from the log. 

This is substantially difficult compared to the easy way - message logs.

#### Hard way - Flutter disassembly
The original application is implemented in [Flutter](https://flutter.dev), so direct application reversing is quite painful process. 
Flutter compiles the source language (TypeScript I guess) to a binary form. It runs on top of a Flutter virtual machine, thus
compiled binary has only one primary entry point, a dispatch function. Disassembly does not yield anything sensible,
it requires special tools. Also, decompilation tools require the Flutter version to precisely match the version used to compile the application.

For those willing to spend time on this: [1](https://tinyhack.com/2021/03/07/reversing-a-flutter-app-by-recompiling-flutter-engine/),
[2](https://www.programmersought.com/article/28206180369/), 
[3](https://rloura.wordpress.com/2020/12/04/reversing-flutter-for-android-wip/),
[4](https://blog.tst.sh/reverse-engineering-flutter-apps-part-1/).


### Donate

Thanks for considering donation if you find this project useful:

#### Bitcoin
```
1DBr1tfuqv6xphg5rzNTPxqiUbqbRHrM2E
```

(No Lightning for now, hopefully soon)

#### Monero
  
```
87KDQUP7yVKd7inmX2WXuaQUBrxeGN9X9AuQwfaUkJ3KQXSRe6KbhnLRvWNK4mx2SeBwcFdHYgS71fzYFS5mtNf7Dn8SdpJ
```
  
#### PayPal
[PayPal link](https://www.paypal.com/donate?hosted_button_id=LC2LK4FGHSUCQ)

