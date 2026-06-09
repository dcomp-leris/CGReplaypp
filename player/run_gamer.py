'''
# 

'''

import os, time
import subprocess
import multiprocessing, yaml

import os

#gamer1_path = os.path.join(os.path.dirname(__file__), "cg_gamer1.py")
#subprocess.run(["sudo", "python3", gamer1_path], check=True)



def run_player1():
    """Run the player Python script."""
    print("Player1 is running ....")
    subprocess.run(["python3", "./cg_gamer1.py"], check=True)

'''
def run_player2():
    """Run the player Python script."""
    print("Player2 is running ....")
    subprocess.run(["sudo","python3", "/home/leris/mygamer/tofino/player_tofino2.py"], check=True)


def run_player3():
    """Run the player Python script."""
    print("Player3 is running ....")
    subprocess.run(["sudo","python3", "/home/leris/mygamer/tofino/player_tofino3.py"], check=True)
'''
def run_tshark(inter = "player-eth0", file_path = "./my.pcap", duration=None):
    """Run tshark command that requires sudo privileges."""
    cmd = ["sudo", "tshark", "-i", inter]
    if duration:
        cmd += ["-a", f"duration:{duration}"]
    cmd += ["-w", file_path]
    subprocess.run(cmd, check=True)
    print("Tshark is running ....")

'''
def run_kill_ports():
    subprocess.run(["sudo","/home/leris/mygamer/tofino/port_clean1.sh"], check=True)
    print("killed the ports ***")
    time.sleep(1)

def run_delete_frames1():
    subprocess.run(["sudo","rm", "-f", "/home/leris/mygamer/tofino/rcv_forza_f/*.*"], check=True)
    print("Removed RCV Frames1 ***")
    time.sleep(1)

def run_delete_frames2():
    subprocess.run(["sudo","rm", "-f", "/home/leris/mygamer/tofino/rcv_forza_s/*.*"], check=True)
    print("Removed RCV Frames2 ***")
    time.sleep(1)

def run_delete_frames3():
    subprocess.run(["sudo","rm", "-f", "/home/leris/mygamer/tofino/rcv_forza_t/*.*"], check=True)
    print("Removed RCV Frames3 ***")    
    time.sleep(1)


def run_delete_pcap():
    subprocess.run(["sudo","rm", "-f", "/home/leris/mygamer/tofino/mypcap/my.pcap"], check=True)
    print("Removed PCAP Files ***")
''' 

def remove_log_files(result_queue=None):
    try:
        # Load config to get received_frames path
        with open("../config/config.yaml", "r") as file:
            config = yaml.safe_load(file)
        
        received_frames = config["gamer"]["received_frames"]
        
        # Ensure received_frames directory exists and clean it
        if not os.path.exists(received_frames):
            os.makedirs(received_frames)
        elif os.path.exists(received_frames):
            subprocess.run(f"rm -f {received_frames}/*", shell=True, check=True)
        
        # Remove specific log files
        #subprocess.run(["rm", "-f", "/home/alireza/mycg/CGReplay/player/logs/my.pcap"], check=True)
        #subprocess.run(["rm", "-f", "/home/alireza/mycg/CGReplay/player/logs/ratelog_CG.txt"], check=True)
        #subprocess.run(["rm", "-f", "/home/alireza/mycg/CGReplay/player/logs/timelog_CG.txt"], check=True)
        
        if result_queue:
            result_queue.put("Log files removed successfully.")
    except Exception as e:
        if result_queue:
            result_queue.put(f"Error removing log files: {e}")



def load_config(file_path="config.txt"):
    """Reads the config.txt file and returns a dictionary of settings."""
    config = {}
    with open(file_path, 'r') as file:
        for line in file:
            # Skip empty lines and comments
            if line.strip() and not line.strip().startswith("#"):
                key, value = line.split("=")
                key = key.strip()
                value = value.strip()
                # Parse resolution into a tuple
                if key == "resolution":
                    value = tuple(map(int, value.split(",")))
                elif key in ["fps", "bitrate", "player_port", "my_test_port", "cg_server_port"]:
                    value = int(value)  # Convert numeric values
                config[key] = value
    return config

if __name__ == "__main__":
    
    with open("../config/config.yaml", "r") as file:
        config = yaml.safe_load(file)

    NIC = config["gamer"]["player_interface"]
    # Final PCAP path (host-visible), relative to CGReplay/player by default
    player_dir = os.path.dirname(os.path.abspath(__file__))
    final_pcap_cfg = config["gamer"]["pcap_file"]
    if os.path.isabs(final_pcap_cfg):
        final_pcap_path = final_pcap_cfg
    else:
        final_pcap_path = os.path.abspath(os.path.join(player_dir, final_pcap_cfg))

    # Internal capture path for Mininet: always use /tmp, derived from the final filename
    pcap_capture_path = os.path.join("/tmp", os.path.basename(final_pcap_path))

    capturing_options = config.get("capturing_options", {})
    enable_pcap = capturing_options.get("enable_pcap", False)
    duration = config.get("Running", {}).get("duration")

    result_queue = multiprocessing.Queue()
    remove_process = multiprocessing.Process(target=remove_log_files, args=(result_queue,))
    player1_process = multiprocessing.Process(target=run_player1)
    tshark_process = None
    if enable_pcap:
        tshark_process = multiprocessing.Process(
            target=run_tshark,
            args=(NIC, pcap_capture_path, duration),
        )

    print('\n')
    print('+++++++++++++++++++++++++')

    remove_process.start()
    remove_process.join()
    # Report result
    if not result_queue.empty():
        print(result_queue.get())
    else:
        print("No result from log file removal process.")

    player1_process.start()
    if tshark_process is not None:
        tshark_process.start()

    player1_process.join()
    if tshark_process is not None:
        tshark_process.join()
