# WalkingPad controller

Simple python script that can control KingSmith WalkingPad A1.
Communicates via [Bluetooth LE GATT](https://www.oreilly.com/library/view/getting-started-with/9781491900550/ch04.html).


## Features

- Switch mode: Standby / Manual / Automatic
- Start belt, stop belt
- Change belt speed (0.5 - 6.0), all options work, e.g. 1.2
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
- Ask for last stored state

## Demo

For the best understanding start jupyter-notebook and take a look at [belt_control.ipynb](belt_control.ipynb)

```bash
# Install jupyter-notebook
pip3 install jupyter

# Start jupyter-notebook in this repository
jupyter-notebook .

# open belt_control.ipynb
```

Controlling script is not yet implemented. 
Play with the notebook.

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
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    WalkingPad controller

-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
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
$> speed 30
$> stop
$> start
$> speed 30
$> status
```

Due to nature of the BluetoothLE callbacks being executed on the main thread we cannot use readline to read from the console.
(At least I have currently no idea how to do it).

