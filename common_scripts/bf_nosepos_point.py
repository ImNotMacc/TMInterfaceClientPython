EVAL_TIME_MIN = 4250
EVAL_TIME_MAX = 4250
MIN_NOSEPOS_DEG = 10
POINT = [523, 9, 458]

MIN_SPEED_KMH = 0
MIN_CP = 0
MIN_WHEELS_ON_GROUND = 0

import math
import numpy
import sys

from tminterface.structs import BFEvaluationDecision, BFEvaluationInfo, BFEvaluationResponse, BFPhase
from tminterface.interface import TMInterface
from tminterface.client import Client, run_client

class MainClient(Client):
    def __init__(self) -> None:
        self.best_angle = -1
        self.best_distance = -1
        self.best_time = -1
        self.curr_angle = -1
        self.curr_distance = -1
        self.curr_time = -1

    def on_registered(self, iface: TMInterface) -> None:
        print(f'Registered to {iface.server_name}')
        iface.execute_command('set controller bruteforce')

    def on_simulation_begin(self, iface):
        self.lowest_time = iface.get_event_buffer().events_duration
        print(f"Base run time: {self.lowest_time}")
        if not (EVAL_TIME_MIN <= EVAL_TIME_MAX <= self.lowest_time):
            print("ERROR: MUST HAVE 'EVAL_TIME_MIN <= EVAL_TIME_MAX <= REPLAY_TIME'")

    def on_bruteforce_evaluate(self, iface, info: BFEvaluationInfo) -> BFEvaluationResponse:
        self.curr_time = info.time
        self.phase = info.phase

        response = BFEvaluationResponse()
        response.decision = BFEvaluationDecision.DO_NOTHING
        
        if self.phase == BFPhase.INITIAL:
            if self.is_eval_time() and self.is_better(iface):
                self.best_angle = self.curr_angle
                self.best_distance = self.curr_distance
                self.best_time = self.curr_time

            # if self.is_max_time():
            #     print(f"base at {self.best_time}: {self.best_angle=}")

        if self.phase == BFPhase.SEARCH:
            if self.is_eval_time() and self.is_better(iface):
                response.decision = BFEvaluationDecision.ACCEPT
                        
            if self.is_past_eval_time():
                if response.decision != BFEvaluationDecision.ACCEPT:
                    response.decision = BFEvaluationDecision.REJECT
            
        return response

    def is_better(self, iface):
        state = iface.get_simulation_state()

        # Conditions
        #x, y, z = state.position
        #x1, y1, z1, x2, y2, z2 = TRIGGER
        #if not (min(x1,x2) < x < max(x1,x2) and min(y1,y2) < y < max(y1,y2) and min(z1,z2) < z < max(z1,z2)):
        #    return False

        # if state.position[0] > 300:
        #     return False

        if MIN_SPEED_KMH > numpy.linalg.norm(state.velocity) * 3.6:
            return False

        if MIN_CP > get_nb_cp(state):
            return False

        if MIN_WHEELS_ON_GROUND > nb_wheels_on_ground(state):
            return False

        car_yaw, car_pitch, car_roll = state.yaw_pitch_roll

        target_yaw = math.atan2(state.velocity[0], state.velocity[2])
        target_pitch = to_rad(90)
        target_roll = to_rad(0)

        # Customize diff_yaw
        strategy = "any"

        if strategy == "any":
            # any angle
            diff_yaw = to_deg(abs(car_yaw - target_yaw))
            # [-90; 90]° yaw should be good enough to nosebug, so 100° should only be considered 10° away in the formula
            diff_yaw = max(diff_yaw - 90, 0)

        else:
            # define the yaw angle you want in degrees, from -90 to -90
            extra_yaw = 0
            target_yaw += to_rad(extra_yaw)
            diff_yaw = to_deg(abs(car_yaw - target_yaw))

        self.curr_angle = diff_yaw + to_deg(abs(car_pitch - target_pitch)) + to_deg(abs(car_roll - target_roll))
        self.curr_distance = distance_to_point(state.position)

        return self.check_angle_and_distance()

    def check_angle_and_distance(self):
        if self.best_angle < 0:
            # 1st run satisfying conditions
            print(f"Base angle: {self.curr_angle}°")
            return True
        
        if MIN_NOSEPOS_DEG < self.best_angle:
            # Nosepos not good enough yet
            if self.curr_angle < self.best_angle:
                if self.phase == BFPhase.INITIAL:
                    print(f"Found lower angle: {self.curr_angle}°")
                return True

        else:
            # Nosepos good enough, reduce distance
            if self.curr_angle < MIN_NOSEPOS_DEG and self.curr_distance < self.best_distance:
                if self.phase == BFPhase.INITIAL:
                    print(f"Found lower distance: {self.curr_distance}m")
                return True
                    
        return False

    def is_eval_time(self):
        return EVAL_TIME_MIN <= self.curr_time <= EVAL_TIME_MAX

    def is_past_eval_time(self):
        return EVAL_TIME_MAX <= self.curr_time

    def is_max_time(self):
        return EVAL_TIME_MAX == self.curr_time

def to_rad(deg):
    return deg / 180 * math.pi

def to_deg(rad):
    return rad * 180 / math.pi

def get_nb_cp(state):
    return len([cp_time.time for cp_time in state.cp_data.cp_times if cp_time.time != -1])

def distance_to_point(pos):
    return (pos[0]-POINT[0]) ** 2 + (pos[1]-POINT[1]) ** 2 + (pos[2]-POINT[2]) ** 2

def nb_wheels_on_ground(state):
    number = 0
    for wheel in state.simulation_wheels:
        if wheel.real_time_state.has_ground_contact:
            number += 1

    return number

def main():
    server_name = f'TMInterface{sys.argv[1]}' if len(sys.argv) > 1 else 'TMInterface0'
    print(f'Connecting to {server_name}...')
    run_client(MainClient(), server_name)

if __name__ == '__main__':
    main()