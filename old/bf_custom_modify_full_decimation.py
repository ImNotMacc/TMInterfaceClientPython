from dataclasses import dataclass
# import glob
import math
# import numpy
import os
import random
from re import T
import struct
import sys
import time
# import pickle

from tminterface.interface import TMInterface
from tminterface.client import Client, run_client
from tminterface.constants import ANALOG_STEER_NAME, BINARY_ACCELERATE_NAME, BINARY_BRAKE_NAME, BINARY_LEFT_NAME, BINARY_RIGHT_NAME, BINARY_RESPAWN_NAME, BINARY_RACE_FINISH_NAME
from tminterface.eventbuffer import EventBufferData
from tminterface.commandlist import CommandList, InputCommand, InputType

from SUtil import Input, Change, Rule, Eval, Optimize, MinMax, Car, Goal, get_dist_2_points, ms_to_sec, sec_to_ms, add_events_in_buffer
from save_load_state import load_state

"""START OF PARAMETERS (you can change here)"""
rules = []

FILL_INPUTS = True
end   = "24:59.10"
start = "24:56.75"
# start = ms_to_sec(int(sec_to_ms(end)) - 5500)

if FILL_INPUTS:
    proba = 0.01
else:
    proba = 0.2
    rules.append(Rule(Input.STEER, Change.TIMING, proba=proba, start_time=start, end_time=end, diff=50))

# proba = 0.01
# start = "22:10.00"
rules.append(Rule(Input.STEER, Change.STEER_, proba=proba, start_time=start, end_time=end, diff=65536))
rules.append(Rule(Input.UP___, Change.TIMING, proba=0.1, start_time=start, end_time=end, diff=50))
rules.append(Rule(Input.DOWN_, Change.TIMING, proba=0.1, start_time=start, end_time=end, diff=50))
# rules.append(Rule(Input.STEER, Change.TIMING, proba=0.2 , start_time=start, end_time=end, diff=50))

LOCK_BASE_RUN = False
LOAD_INPUTS_FROM_FILE = "work.txt"
LOAD_REPLAY_FROM_STATE = "state99.bin"
# LOAD_REPLAY_FROM_STATE = ""

# steer_cap_accept = True
p = 0.3
steer_equal_last_input_proba = p # proba to make a steer equal to last steer
steer_zero_proba = 0.5 # proba to set steer to 0 instead of changing direction left/right
steer_full_proba = p

# From previous script
eval = Eval.TIME
parameter = Optimize.CUSTOM

TIME = end
TIME_MIN = int(sec_to_ms(TIME))
TIME_MAX = int(sec_to_ms(TIME))

# eval == Eval.CP:
CP_NUMBER = 93

# parameter == Optimize.DISTANCE:
POINT_POS = [295, 73.5, 760]

# Min diff to consider an improvement worthy
min_diff = 0.01

SYNTAX_MS = False
"""END OF PARAMETERS"""

for rule in rules:
    rule.init()

lowest_poss_change = min([c.start_time for c in rules])
highest_poss_change = max([c.end_time for c in rules])

if not lowest_poss_change <= highest_poss_change:
    print("ERROR: MUST HAVE 'lowest_poss_change <= highest_poss_change'")


class MainClient(Client):
    def __init__(self) -> None:
        self.best_precise_time = -1
        self.finished = False
        self.state_min_change = None
        # self.states = []
        self.begin_buffer = None
        self.current_buffer = None
        # self.base_velocity = None
        self.best_coeff = -1
        self.nb_iterations = 0
        self.cp_count = 0
        self.force_reject = False
        self.car = None
        # self.best_state = None
        self.best_car = None
        self.pre_rewind_buffer = None

    def on_registered(self, iface: TMInterface) -> None:
        print(f'Registered to {iface.server_name}')
        print(f"Randomizing inputs between {lowest_poss_change} and {highest_poss_change}")
        for rule in rules:
            print(rule)

    def on_deregistered(self, iface: TMInterface) -> None:
        print(f'Deregistered from {iface.server_name}')

    def on_simulation_begin(self, iface):
        # print("on_simulation_begin start")
        iface.remove_state_validation()
        # iface.set_timeout(2000)

        self.begin_buffer = iface.get_event_buffer()
        self.lowest_time = self.begin_buffer.events_duration

        if eval == Eval.TIME:
            if not (TIME_MIN <= TIME_MAX <= self.lowest_time):
                print("ERROR: MUST HAVE 'TIME_MIN <= TIME_MAX <= REPLAY_TIME'")

        # if self.lowest_time < TIME_MAX + 1000:
        #     iface.set_simulation_time_limit(TIME_MAX + 1000)
        #     a = self.lowest_time
        #     self.lowest_time = TIME_MAX + 1000
        #     self.begin_buffer.events_duration = self.lowest_time
        #     events = self.begin_buffer.find(event_name=BINARY_RACE_FINISH_NAME)
        #     events = self.begin_buffer.find(time=a)
        #     print(len(events))
        #     events[0].time = self.lowest_time + 100010

        # Fill begin_buffer
        self.begin_buffer = iface.get_event_buffer()
        if LOAD_INPUTS_FROM_FILE:
            self.pre_rewind_buffer = EventBufferData(self.lowest_time)
            self.pre_rewind_buffer.control_names = self.begin_buffer.control_names
            self.load_inputs_from_file()
            # iface.set_event_buffer(self.begin_buffer) # COMMENT FOR PARTIAL BUFFER AND DELETE FOR RUN/SIMU STATES
        else:
            # input command sorted
            # dichotomy dans les input command
            # trouver l'index de séparation
            # prerewind = [:i] et begin = [i:]
            self.pre_rewind_buffer = EventBufferData(self.lowest_time)
            self.pre_rewind_buffer.control_names = self.begin_buffer.control_names

        if FILL_INPUTS:
            self.fill_inputs(lowest_poss_change, highest_poss_change)

        self.current_buffer = EventBufferData(self.lowest_time)
        self.current_buffer.control_names = self.begin_buffer.control_names
        # self.current_buffer = self.begin_buffer.copy() # copy avoids timeout?
        # print(self.begin_buffer.to_commands_str())
            
        # print("on_simulation_begin end")
        pass
        
    def fill_inputs(self, start_fill=0, end_fill=0):
        """Fill inputs between start_fill and end_fill included"""
        if end_fill == 0:
            end_fill = self.begin_buffer.events_duration
        
        # print(f"fill_inputs(self, {start_fill}, {end_fill})")
        # Find start steering (if start fill_inputs not on a steering change)
        if LOAD_INPUTS_FROM_FILE:
            buffer = self.pre_rewind_buffer
        else:
            buffer = self.begin_buffer

        curr_steer = 0
        for event_time in range(start_fill, -10, -10):
            # print(f"event_time={event_time}")
            events_at_time = buffer.find(time=event_time, event_name=ANALOG_STEER_NAME)
            if len(events_at_time) > 0:
                if len(events_at_time) > 1:
                    print(f"dirty inputs at {event_time}: len={len(events_at_time)}")
                curr_steer = events_at_time[-1].analog_value
                # print(f"start steer={curr_steer}")
                break

        # Fill inputs
        for event_time in range(start_fill, end_fill+10, 10):
            events_at_time = self.begin_buffer.find(time=event_time, event_name=ANALOG_STEER_NAME)
            if len(events_at_time) > 0:
                if len(events_at_time) > 1:
                    print(f"dirty inputs at {event_time}: len={len(events_at_time)}")
                curr_steer = events_at_time[-1].analog_value
            else:
                self.begin_buffer.add(event_time, ANALOG_STEER_NAME, curr_steer)
        
    def on_simulation_step(self, iface: TMInterface, _time: int):
        # print("on_simulation_step start")
        self.race_time = _time
        if not self.state_min_change:
            if _time == 100 and LOAD_REPLAY_FROM_STATE:
                self.load_replay_from_state(iface, LOAD_REPLAY_FROM_STATE)

            if LOAD_INPUTS_FROM_FILE and not LOAD_REPLAY_FROM_STATE:
                if self.race_time % 10000 == 0:
                    sys.stdout.write(f"\rSimulating base run... {int(self.race_time/1000)}sec")
                    sys.stdout.flush()
                if self.race_time == lowest_poss_change - 10:
                    print()
                    print(f"Simulation done")
                    
                    # This line sets base run as the inputs file instead of the replay
                    # iface.set_event_buffer(self.begin_buffer)
            else:
                # When loading inputs from a long replay, we can't load them all at the start because TMI timeout
                # So we load the inputs when they happen -> doesn't work with simu save state
                # events_at_time = self.begin_buffer.find(time=self.race_time)
                # for event in events_at_time:
                #     add_events_in_buffer(events_at_time, self.current_buffer)
                pass

            if self.race_time == lowest_poss_change - 10:
                # Store state to rewind to for every iteration, for now it is earliest possible input change
                # lowest_poss_change-10 because state contains inputs and we don't update state with 1st input
                self.state_min_change = iface.get_simulation_state()

                print(f"self.state_min_change created at {ms_to_sec(self.race_time)}")

        if self.is_eval_time():
            # print("eval_time")
            state = iface.get_simulation_state()
            # state.timee = _time
            if self.is_better(state):
                # self.best_state = state
                self.best_car = self.car
                
                if self.nb_iterations == 0:
                    if LOAD_INPUTS_FROM_FILE:
                        # print() # after write/flush
                        # print(f"base = {self.race_time}")
                        pass
                else:
                    # print(f"FOUND IMPROVEMENT: {race_time}")
                    if not LOCK_BASE_RUN:
                        self.begin_buffer.events = self.current_buffer.events
                
                    # Save inputs to file
                    self.save_result()

        # Wait until the end of eval time before rewinding, in case an even better state is found later on
        if self.is_past_eval_time():
            # print("past eval_time")
            self.start_new_iteration(iface)

    def condition(self):
        """Returns False if conditions are not met so run is rejected"""
        return True
        
    def is_better(self, state):
        self.car = Car(self.race_time)
        self.car.update(state)

        # if there's no best car, then it's base run
        base_run = not self.best_car

        if not self.condition():
            return False
            
        if parameter == Optimize.TIME:
            return self.is_earlier(base_run, min_diff)

        if parameter == Optimize.DISTANCE:
            return self.is_closer(base_run, min_diff)

        if parameter == Optimize.VELOCITY:
            return self.is_faster(base_run, min_diff)

        if parameter == Optimize.CUSTOM:
            return self.is_custom(base_run, min_diff)

        return False

    def is_custom(self, base_run, min_diff=0):
        """Evaluates if the iteration is better when parameter == Optimize.CUSTOM"""
        # if base_run:
        #     car = self.best_car
        # else:
        #     car = self.car

        # condition
        # turtle
        # if not abs(self.car.pitch_deg) > math.pi/2:
        #     return False
        if not abs(self.car.pitch_rad) + abs(self.car.roll_rad) < 1:
            return False
        # # print(self.car.y)
        # if not 959 < self.car.x:
        #     return False
        if not 93 < self.car.y:
            return False
        # if not 725 < self.car.z < 733:
        #     return False
        # if not self.car.yaw_deg > 70:
        #     return False
        # if not self.cp_count >= 85:
        #     return False

        # self.car.custom = abs(car.pitch_deg - 90)
        # self.car.custom = self.car._time
        self.car.custom = self.car.vel_y*0.5 - self.car.vel_z
        # self.car.custom = get_dist_2_points(POINT_POS, self.car.position, "xz")
        # self.car.custom = self.car.get_speed("xz")
        
        if base_run:
            print(f"Base run custom = {self.car.custom}")
            return True
        elif self.car.custom > self.best_car.custom + min_diff:
            print(f"Improved custom = {self.car.custom}")
            return True

        return False

    def is_custom2(self, base_run, min_diff=0):
        """Evaluates if the iteration is better when parameter == Optimize.CUSTOM"""        
        # Goal 1: max car.y until car.y > 49
        # Goal 2: min car.x
        if base_run:
            print(f"Base run custom y = {self.car.y}")
            return True
        else:
            if self.best_car.y < 48.5:
                if self.car.y > self.best_car.y + min_diff:
                    print(f"Improved custom y = {self.car.y}")
                    return True
            else:
                if self.car.y > 48.5 and self.car.x < self.best_car.x - min_diff:
                    print(f"Improved custom x = {self.car.x}")
                    return True
                    
        return False

    def is_custom3(self, base_run, min_diff=0):
        """Evaluates if the iteration is better when parameter == Optimize.CUSTOM"""        
        # Goal 1: max car.y until car.y > 49
        # Goal 2: min car.x until car.x < 414
        # Goal 3: min time
        # condition
        # if not 138 < self.car.x:
        #     return False
        # if not 63 < self.car.y:
        #     return False
        if not 102 < self.car.z < 104:
            return False
        # if not self.cp_count >= 73:
        #     return False
            
        # if self.car_time == int(sec_to_ms("18:47.50")) and 55 < self.car.y:
        #     self.force_reject = True
        #     return False

        goals = []
        goals.append(Goal("x", MinMax.MIN, 0))
        goals.append(Goal("_time", MinMax.MIN, 0))

        if base_run:
            for goal in goals:
                print(f"Base run custom {goal.variable} = {getattr(self.car, goal.variable)}")
            return True
        else:
            for goal in goals:
                if goal.achieved(self.best_car):
                    if goal.achieved(self.car):
                        continue
                    else:
                        return False
                else:
                    if goal.closer(self.car, self.best_car, min_diff):
                        print(f"Improved custom {goal.variable} = {getattr(self.car, goal.variable)}")
                        return True
                    else:
                        return False
                    
        return False
        
    def is_earlier(self, base_run, min_diff=0):
        # if base_run:
        #     car = self.best_car
        # else:
        #     car = self.car

        # if self.best_car and self.car._time < self.best_car._time:
        #     print(f"FOUND IMPROVEMENT: {self.car._time}")
        #     return True

        # condition
        # if not self.car.x < 380:
        #     return False
        # if not 100 < self.car.z < 125:
        #     return False
        # if abs(self.car.pitch_rad) > 0.1:
        #     return False
        # if not 91.5 < self.car.y:
        #     return False
        # if not self.car.vel_x > 5:
        #     return False
        # if not self.car.yaw_deg > 80:
        #     return False

        if base_run:
            print(f"Base run time = {ms_to_sec(self.car._time - 10)}")
            return True
        elif self.car._time < self.best_car._time - min_diff:
            print(f"Improved time = {ms_to_sec(self.car._time - 10)}")
            return True
        
        return False
    
    def is_closer(self, base_run, min_diff=0, axis="xyz"):
        # if base_run:
        #     car = self.best_car
        # else:
        #     car = self.car

        self.car.distance = get_dist_2_points(POINT_POS, self.car.position, axis)
        
        if base_run:
            print(f"Base run distance = {math.sqrt(self.car.distance)} m")
            return True
        elif self.car.distance < self.best_car.distance - min_diff:
            print(f"Improved distance = {math.sqrt(self.car.distance)} m")
            return True
        
        return False
        
    def is_faster(self, base_run, min_diff=0):
        # if base_run:
        #     car = self.best_car
        # else:
        #     car = self.car

        self.car.velocity = min(self.car.speed_kmh, 1000)

        if base_run:
            print(f"Base run velocity = {self.car.velocity} kmh")
            return True
        elif self.car.velocity > self.best_car.velocity + min_diff:
            print(f"Improved velocity = {self.car.velocity} kmh")
            return True
        
        return False

    def is_eval_time(self):
        if eval == Eval.TIME:
            # print(self.current_time)
            if TIME_MIN <= self.race_time <= TIME_MAX:
                return True
        if eval == Eval.CP:
            if CP_NUMBER <= self.cp_count:
                return True
        
        return False

    def is_past_eval_time(self):
        if eval == Eval.TIME:
            if TIME_MAX <= self.race_time:
                return True

        if eval == Eval.CP:
            if CP_NUMBER <= self.cp_count or (self.best_car and self.race_time == self.best_car._time):
                # self.cp_count = 0
                return True
        
        if self.force_reject:
            self.force_reject = False
            return True
        
        return False

    def start_new_iteration(self, iface):
        # print("start_new_iteration")
        """Randomize and rewind"""
        self.randomize_inputs()
        iface.set_event_buffer(self.current_buffer)

        if not self.state_min_change:
            print("no self.state_min_change to rewind to")
            sys.exit()
        iface.rewind_to_state(self.state_min_change)

        self.cp_count = -1
        # print(f"{self.cp_count=}")
        self.nb_iterations += 1
        if self.nb_iterations in [1, 10, 100] or self.nb_iterations % 1000 == 0:
            print(f"{self.nb_iterations=}")

    def randomize_inputs(self):
        """Restore base run events (with deepcopy) and randomize them using rules.
        Deepcopy can't use EventBufferData.copy() because events is deepcopied but not the individual events"""
        
        # Restore events from base run (self.begin_buffer.events) in self.current_buffer.events using deepcopy
        self.current_buffer.clear()
        add_events_in_buffer(self.begin_buffer.events, self.current_buffer)
        # for event in self.begin_buffer.events:
        #     event_time = event.time - 100010
        #     event_name = self.begin_buffer.control_names[event.name_index]
        #     event_value = event.analog_value if "analog" in event_name else event.binary_value
        #     self.current_buffer.add(event_time, event_name, event_value)

        # Apply rules to self.current_buffer.events
        for rule in rules:
            # only inputs that match the rule (ex: steer)
            # try:
            #     print(rule.input)
            #     print(rule.input.name)
            #     print(rule.input.value)
            # except:
            #     pass
            events = self.current_buffer.find(event_name=rule.input.value)
            last_steer = 0
            for event in events:
                event_realtime = event.time - 100010
                # event in rule time
                if rule.start_time <= event_realtime <= rule.end_time:
                    # event proba
                    if random.random() < rule.proba:
                        # event type
                        if rule.change_type == Change.STEER_:
                            if random.random() < steer_equal_last_input_proba:
                                event.analog_value = last_steer
                            else:
                                new_steer = event.analog_value + random.randint(-rule.diff, rule.diff)
                                # if diff makes steer change direction (left/right), try 0
                                if (event.analog_value < 0 < new_steer or new_steer < 0 < event.analog_value) and random.random() < steer_zero_proba:
                                    event.analog_value = 0
                                else:
                                    event.analog_value = new_steer
                                event.analog_value = min(event.analog_value, 65536)
                                event.analog_value = max(event.analog_value, -65536)
                                
                        if rule.change_type == Change.TIMING:
                            # ms -> 0.01
                            diff = random.randint(-rule.diff/10, rule.diff/10)
                            # 0.01 -> ms
                            event.time += diff*10

                if Input.STEER.name == self.begin_buffer.control_names[event.name_index]:
                    last_steer = event.analog_value
        
    def save_result(self, time_found="", file_name="result.txt"):
        if time_found == "":
            time_found = self.race_time
        
        # Gather inputs        
        inputs_str = ""
        # if LOAD_INPUTS_FROM_FILE:
        if self.pre_rewind_buffer:
            # inputs before inputs_min_time
            inputs_str += self.pre_rewind_buffer.to_commands_str()
            inputs_str += "\n"

        inputs_str += self.current_buffer.to_commands_str()
        
        # Convert inputs
        if not SYNTAX_MS:
            inputs_str = to_sec(inputs_str)
            
        # Header
        inputs_str = f"# Time: {time_found}, iterations: {self.nb_iterations}\n" + inputs_str
        
        # Footer
        inputs_str += f"0 load_state state.bin\n"
        inputs_str += f"0 set draw_game false\n"
        inputs_str += f"0 set speed 100\n"
        inputs_str += f"{start} set draw_game true\n"
        inputs_str += f"{start} set speed 1\n"

        # Write inputs in file
        res_file = os.path.expanduser('~/Documents') + "/TMInterface/Scripts/" + file_name
        with open(res_file, "w") as f:
            f.write(inputs_str)

    def load_inputs_from_file(self, file_name="inputs.txt"):
        # Clear and re-fill the buffer (to keep control_names and event_duration: worth?)
        self.begin_buffer.clear()
        self.pre_rewind_buffer.clear()

        inputs_file = os.path.expanduser('~/Documents') + "/TMInterface/Scripts/" + file_name
        cmdlist = CommandList(open(inputs_file, 'r'))
        commands = [cmd for cmd in cmdlist.timed_commands if isinstance(cmd, InputCommand)]

        for command in commands:
            if   command.input_type == InputType.UP:      command.input = BINARY_ACCELERATE_NAME
            elif command.input_type == InputType.DOWN:    command.input = BINARY_BRAKE_NAME
            elif command.input_type == InputType.LEFT:    command.input = BINARY_LEFT_NAME
            elif command.input_type == InputType.RIGHT:   command.input = BINARY_RIGHT_NAME
            elif command.input_type == InputType.RESPAWN: command.input = BINARY_RESPAWN_NAME
            elif command.input_type == InputType.STEER:   command.input = ANALOG_STEER_NAME
            else: print(f"{command.input_type=}"); continue

            if command.timestamp < lowest_poss_change:
                self.pre_rewind_buffer.add(command.timestamp, command.input, command.state)
            else:
                self.begin_buffer.add(command.timestamp, command.input, command.state)

    def load_replay_from_state(self, iface, file_name=""):
        """        """        
        if not file_name:
            print("No simu_state file suitable to load")
        else:
            print(f"Loading {file_name}")
            self.state_file = os.path.expanduser('~/Documents') + "/TMInterface/States/" + file_name
            # self.state_min_change = load_state(self.state_file)
            iface.rewind_to_state(load_state(self.state_file))
            
            # update self.race_time with time from simu state
            self.race_time = iface.get_simulation_state().time - 2610

            if lowest_poss_change - 10 < self.race_time:
                print("ERROR: simu save_state time must be at least 1 tick before lowest_poss_change")

    def on_checkpoint_count_changed(self, iface: TMInterface, current: int, target: int):
        self.cp_count = current
        if eval == eval.CP:
            # if current == CP_NUMBER:
            #     print(f"Cross CP at {self.race_time}")
            if self.nb_iterations == 0:
                if current == CP_NUMBER:
                    global TIME_MIN
                    global TIME_MAX
                    TIME_MIN = 0 # script won't check before lowest_poss_change anyway
                    TIME_MAX = self.race_time
                    # print(current)
        # print(f'Reached checkpoint {current}/{target}')
        # if current == target:
        #     # print(f'Finished the race at {self.race_time}')
        #     self.finished = True
        #     iface.prevent_simulation_finish()

    def get_nb_cp(self, iface):
        cp_times = iface.get_checkpoint_state().cp_times
        # self.nb_cp = len([time for (time, _) in cp_times if time != -1])
        # print(f"{current} {self.nb_cp=}")
        return len([time for (time, _) in cp_times if time != -1])


def to_sec(inputs_str: str) -> str:
    """Transform a string containing lines of inputs to min:sec.ms format"""

    def ms_to_sec_line(line: str) -> str:
        """Converter ms->sec for entire line"""
        if "." in line or line == "":
            return line
        splits = line.split(" ")
        if "-" in splits[0]:            
            press_time, rel_time = splits[0].split("-")
            splits[0] = ms_to_sec(press_time) + "-" + ms_to_sec(rel_time)
        else:
            splits[0] = ms_to_sec(splits[0])
        return " ".join(splits)

    result_string = ""
    for line in inputs_str.split("\n"):
        if line != "":
            result_string += ms_to_sec_line(line) + "\n"
    
    return result_string

def main():
    server_name = f'TMInterface{sys.argv[1]}' if len(sys.argv) > 1 else 'TMInterface0'
    print(f'Connecting to {server_name}...')
    run_client(MainClient(), server_name)

if __name__ == '__main__':
    main()

"""
To test:
- Input.STEER/UP/DOWN
- if self.race_time == lowest_poss_change:
    if LOAD_INPUTS_FROM_FILE:
        iface.set_event_buffer(self.begin_buffer)

Ideas:
- not rewind to time 0 => rewind to lowest_poss => rewind to 1st change
"""