print("Running LUME BELT SERVICE.....")

get_ipython().run_line_magic('load_ext', 'autoreload')
get_ipython().run_line_magic('autoreload', '2')

from tools import isotime

from belt.tools import NpEncoder
import pandas as pd
import numpy as np

import h5py
import json
import epics

import sys
import os
import toml
from time import sleep, time
import datetime
from belt.evaluate import default_belt_merit
from belt.belt_impact import run_belt, evaluate_belt
from make_dashboard import make_dashboard

import matplotlib.pyplot as plt

import matplotlib as mpl


#Import parameters to set before running

phase_shift = 1.5        # shift in L1 and L2 phase
initial_energy = 75e6    # Energy of the input beam 
input_beam =  "/sdf/data/ad/ard/u/jytang/lume-belt-live/STCAV_data/particle-2025-04-05.h5"  # Input beam generated from STCAV image


# Saving and loading
def save_pvdata(filename, pvdata, isotime):
    with h5py.File(filename, 'w') as h5:
        h5.attrs['isotime'] = np.bytes_(isotime)
        for k, v in pvdata.items():
            if isinstance(v, str):
                v =  np.bytes_(v)
            h5[k] = v 
def load_pvdata(filename):
    
    if not os.path.exists(filename):
        raise ValueError(f'H5 file does not exist: {filename} ')
    pvdata = {}
    with h5py.File(filename, 'r') as h5:
        isotime = h5.attrs['isotime']
        for k in h5:
            v = np.array(h5[k])        
            if v.dtype.char == 'S':
                v = str(v.astype(str))
            pvdata[k] = v
            
    return pvdata, isotime

CSV = 'pv_mapping/lclsii_belt.csv'
DF = pd.read_csv(CSV)#.dropna()

PVLIST = list(DF['device_pv_name'].dropna()) 

LIVE = True
if LIVE:
    MONITOR = {pvname:epics.PV(pvname) for pvname in PVLIST}


def get_snapshot(snapshot_file=None):
        
    if LIVE:
        itime = isotime()
        pvdata =  {k:MONITOR[k].get() for k in MONITOR}
        
    else:
        pvdata, itime = load_pvdata(snapshot_file)
        itime = itime.decode('utf-8')
    
    #logger.info(f'Acquired settings from EPICS at: {itime}')
    
    epics_working_check = [val for val in pvdata.values() if val is None]
    
    if len(epics_working_check) == len(list(pvdata.keys())):
        raise Exception(f'EPICS returned None for all keys. Please check if you are able to connect to Accelerator')

    VCC_Key = None
    
    for k, v in pvdata.items():
        
        if v is None:
            raise ValueError(f'EPICS get for {k} returned None')
        
        if ':IMAGE:ARRAYDATA' in k.upper():
            VCC_Key = k
            found = False
            logger.info(f'Waiting for good {k}')
            counter = 0
            USE_VCC_LOCAL = True
            while not found and counter < 5:
                counter += 1
                if v is None:
                    continue
                if v.std() > 10:
                    found = True
                else:
                    v = MONITOR[k].get()
            if counter == 5:
                logger.info(f'VCC is not working. Defaulting to None.')
                USE_VCC_LOCAL = False
            elif np.ptp(v) < 128:
                v = v.astype(np.int8) # Downcast preemptively 
            pvdata[k] = v
        else:
            USE_VCC_LOCAL = False

    if not USE_VCC_LOCAL and VCC_Key in pvdata:
        del pvdata[VCC_Key]

    return pvdata, itime, USE_VCC_LOCAL

df = DF[DF['device_pv_name'].notna()]
assert len(df) > 0, 'Empty dataframe!'
    
pv_names = list(df['device_pv_name'])

pvdata, itime, USE_VCC_LOCAL = get_snapshot(None)
    
df['pv_value'] = [pvdata[k] for k in pv_names]


def get_settings(csv, base_settings={}, snapshot_dir=None, snapshot_file=None):
    """
    Fetches live settings for all devices in the CSV table, and translates them to simulation inputs
     
    """
    df = DF[DF['device_pv_name'].notna()]
    assert len(df) > 0, 'Empty dataframe!'
    
    pv_names = list(df['device_pv_name'])

    pvdata, itime, USE_VCC_LOCAL = get_snapshot(snapshot_file)
    
    df['pv_value'] = [pvdata[k] for k in pv_names]
    
    # Assign impact
    #df['impact_value'] = df['impact_factor']*df['pv_value'] 
    #if 'impact_offset' in df:
    #    df['impact_value'] = df['impact_value']  + df['impact_offset']

    # Collect settings
    settings = base_settings.copy()


    HL_phase = df.loc[df["Variable"] == "HL_phase", 'pv_value' ].values[0] - 180
    HL_amplitude = df.loc[df["Variable"] == "HL_amplitude", 'pv_value' ].values[0]*1e6
    HL_gradient = HL_amplitude/5.5346304
    
    #HL_energy = df.loc[df["Variable"] == "HL_energy", 'pv_value' ].values[0]
    #HL_chirp = df.loc[df["Variable"] == "HL_chirp", 'pv_value' ].values[0]

    #HL_phase = df.loc[df["Variable"] == "HL_phase", 'pv_value' ].values[0]
    #HL_amplitude = np.abs(HL_energy/np.cos(HL_phase/180*np.pi)*1e6/5.5346304)

    #HL_phase = df.loc[df["Variable"] == "HL_phase_2", 'pv_value' ].values[0]
    #HL_amplitude = df.loc[df["Variable"] == "HL_amplitude", 'pv_value' ].values[0]*1e6
    #HL_amplitude = (df.loc[df["Variable"] == "HL_amplitude_chirponly", 'pv_value' ].values[0] +
    #                df.loc[df["Variable"] == "HL_amplitude_+FBK", 'pv_value' ].values[0] +
    #                df.loc[df["Variable"] == "HL_amplitude_-FBK", 'pv_value' ].values[0] )*1e6
    #HL_gradient = HL_amplitude/5.5346304
    
    L1_energy = df.loc[df["Variable"] == "L1B_energy", 'pv_value' ].values[0]
    L1_chirp = df.loc[df["Variable"] == "L1B_chirp", 'pv_value' ].values[0]

    L1_phase = df.loc[df["Variable"] == "L1B_phase", 'pv_value' ].values[0]
    L1_amplitude = (df.loc[df["Variable"] == "L1B_amp1", 'pv_value' ].values[0] + 
                    df.loc[df["Variable"] == "L1B_amp2", 'pv_value' ].values[0] +
                    df.loc[df["Variable"] == "L1B_amp3", 'pv_value' ].values[0])*1e6
    L1_gradient = L1_amplitude/16.603888
                    
    #L1_amplitude = np.abs((L1_energy - HL_energy)/ np.cos(L1_phase/180*np.pi)*1e6/16.603888)
    #L1_amplitude = np.abs((L1_energy - HL_energy)*1e6/16.603888)



    L2_energy = df.loc[df["Variable"] == "L2B_energy", 'pv_value' ].values[0]
    L2_chirp = df.loc[df["Variable"] == "L2B_chirp", 'pv_value' ].values[0]

    L2_phase = df.loc[df["Variable"] == "L2B_phase", 'pv_value' ].values[0]  
    L2_gradient = np.abs(L2_energy/np.cos(L2_phase/180*np.pi)*1e6/99.623328)
   
    
    

    L3_energy = df.loc[df["Variable"] == "L3B_energy", 'pv_value' ].values[0]
    L3_chirp = df.loc[df["Variable"] == "L3B_chirp", 'pv_value' ].values[0]

    L3_phase = df.loc[df["Variable"] == "L3B_phase", 'pv_value' ].values[0]
    L3_gradient = np.abs(L3_energy/np.cos(L3_phase/180*np.pi)*1e6/166.038878)
    #L3_amplitude = np.abs(L3_energy*1e6/166.038878)
    
    
    BC1_energy = df.loc[df["Variable"] == "BC1_energy", 'pv_value' ].values[0]/1e3
    #BC1_rigidity = (df.loc[df["Variable"] == "BCX11", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX12", 'pv_value' ].values[0] +
    #         df.loc[df["Variable"] == "BCX13", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX14", 'pv_value' ].values[0])/4/10
    #BC1_angle = BC1_rigidity/(3.3356*BC1_energy)

    BC2_energy = df.loc[df["Variable"] == "BC2_energy", 'pv_value' ].values[0]/1e3
    #BC2_rigidity = (df.loc[df["Variable"] == "BCX21", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX22", 'pv_value' ].values[0] +
    #         df.loc[df["Variable"] == "BCX23", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX24", 'pv_value' ].values[0])/4/10
    #BC2_angle = BC2_rigidity/(3.3356*BC2_energy)


    BC1_rigidity = df.loc[df["Variable"] == "BCX11", 'pv_value' ].values[0] /10
    BC1_angle = BC1_rigidity/(3.3356*BC1_energy)

    BC2_rigidity = df.loc[df["Variable"] == "BCX21", 'pv_value' ].values[0]/10

    BC2_angle = BC2_rigidity/(3.3356*BC2_energy)


    #initial_energy = df.loc[df["Variable"] == "Initial_energy", 'pv_value'].values[0]*1e6

    BC1_energy_increment =BC1_energy*1e9 - (initial_energy + L1_amplitude*np.cos(L1_phase/180*np.pi) +  HL_amplitude*np.cos(HL_phase/180*np.pi))
    #BC1_energy_increment =BC1_energy*1e9 - (90e6 + L1_energy*1e6 )
    BC2_energy_increment = BC2_energy*1e9 - (BC1_energy*1e9 + L2_energy*1e6)

    settings["BC1:angle"] = BC1_angle
    settings["BC2:angle"] = BC2_angle
    settings["L1:gradient"] = L1_gradient
    settings["L1:phase_deg"] = L1_phase + phase_shift
    settings["L2:gradient"] = L2_gradient
    settings["L2:phase_deg"] = L2_phase + phase_shift
    settings["L3:gradient"] = L3_gradient
    settings["L3:phase_deg"] = L3_phase 
    settings["HL:gradient"] = HL_gradient
    settings["HL:phase_deg"] = HL_phase 
    settings["EBC1:energy_increment"] = BC1_energy_increment
    settings["EBC2:energy_increment"] = BC2_energy_increment
    
    #if DEBUG:
    #    settings['total_charge'] = 0
    #else:
    #    settings['total_charge'] = 1 # Will be updated with particles

    # VCC image
    #if USE_VCC_LOCAL:
    #    logger.info('Getting VCC Live Distgen')
    #    dfile, img, cutimg = get_live_distgen_xy_dist(filename=DISTGEN_LASER_FILE, vcc_device=VCC_DEVICE, pvdata=pvdata)  
    #    settings['distgen:xy_dist:file'] = dfile
    #elif USE_SAVED_VCC:
    #    settings['distgen:xy_dist:file'] = SAVED_VCC
    #    img, cutimg = None, None
    #else:
    #    img, cutimg = None, None
        #settings['distgen:r_dist:max_r:value'] = 0.35 # TEMP     
        
    if snapshot_dir and not snapshot_file:
        filename = os.path.abspath(os.path.join(snapshot_dir, f'{MODEL}-snapshot-{itime}.h5'))
    #    total_charge_pC = settings['distgen:total_charge:value']
    #    if total_charge_pC < MIN_CHARGE_pC:
    #        logger.info(f'total charge is too low: {total_charge_pC:.2f} pC, not saving snapshot')         
    #    else:
        save_pvdata(filename, pvdata, itime)
    #        logger.info(f'EPICS shapshot written: {filename}')
        
        
    return settings, df, itime

# Patch this into the function below for the dashboard creation
def my_merit(belt_object, itime):
    # Collect standard output statistics
    merit0 = default_belt_merit(belt_object)
    
    PLOT_OUTPUT_DIR_DATED = convertToDatedFormat(PLOT_OUTPUT_DIR)
    #Overriding at runtime to save in dated folders
    DASHBOARD_KWARGS["outpath"] = PLOT_OUTPUT_DIR_DATED
    
    # Make the dashboard from the evaluated object
    plot_file = make_dashboard(belt_object, itime=itime, **DASHBOARD_KWARGS)
    #print('Dashboard written:', plot_file)
    #logger.info(f'Dashboard written: {plot_file}')
    
    # Make all readable
    os.chmod(plot_file, 0o644)
    
    # Assign extra info
    merit0['plot_file'] = plot_file    
    merit0['isotime'] = itime
    
    # Clear any buffers
    plt.close('all')

    return merit0

def convertToDatedFormat(destionation_folder):
    curr_date = datetime.date.today()
    year,month,day = curr_date.strftime('%Y'),curr_date.strftime('%m'),curr_date.strftime('%d')
    destionation_folder_dated = destionation_folder + "/" + year + "/" + month + "/" + day

    if not os.path.exists(destionation_folder_dated):
        os.makedirs(destionation_folder_dated)
    
    return destionation_folder_dated


dat = {}

MODEL = 'LCLSII'
HOST = 's3df'
ARCHIVE_DIR = './archive'

SNAPSHOT_DIR = './snapshot'
SUMMARY_OUTPUT_DIR = './summary'
PLOT_OUTPUT_DIR = './plot'
#SETTINGS0 = {}
#SETTINGS0 = {"Impact_particles": "/sdf/data/ad/ard/u/jytang/lume-belt-live/impact_particles/final_particles.h5", "num_doublings": 7}
#SETTINGS0 = {"Impact_particles": "/sdf/data/ad/ard/u/jytang/lume-belt-live/STCAV_data/particle-2025-04-05.h5"}
SETTINGS0 = {"Impact_particles": input_beam}
CONFIG0 = {"input": "example/belt.in", "workdir": os.environ.get("SCRATCH")}
PREFIX = f'lume-belt-live-demo-{HOST}-{MODEL}'


DASHBOARD_KWARGS = {'outpath':PLOT_OUTPUT_DIR,            
                    'name' : PREFIX
                   }    

SNAPSHOT = None
SNAPSHOT_DIR_DATED = convertToDatedFormat(SNAPSHOT_DIR)
ARCHIVE_DIR_DATED = convertToDatedFormat(ARCHIVE_DIR)
SUMMARY_OUTPUT_DIR_DATED = convertToDatedFormat(SUMMARY_OUTPUT_DIR)
settings, df, itime = get_settings(CSV,
                                                           SETTINGS0,
                                                           snapshot_dir=SNAPSHOT_DIR_DATED,
                                                          snapshot_file=SNAPSHOT)       

def run1():
    dat = {}

    SNAPSHOT_DIR_DATED = convertToDatedFormat(SNAPSHOT_DIR)
    ARCHIVE_DIR_DATED = convertToDatedFormat(ARCHIVE_DIR)
    SUMMARY_OUTPUT_DIR_DATED = convertToDatedFormat(SUMMARY_OUTPUT_DIR)
        
    # Acquire settings
    mysettings, df,  itime = get_settings(CSV,
                                                           SETTINGS0,
                                                           snapshot_dir=SNAPSHOT_DIR_DATED,
                                                          snapshot_file=SNAPSHOT)        
    print(mysettings)
    dat['isotime'] = itime
    
    # Record inputs
    dat['inputs'] = mysettings
    dat['config'] = CONFIG0
    dat['pv_mapping_dataframe'] = df.to_dict()
    
    #logger.info(f'Running evaluate_impact_with_distgen...')

    t0 = time()
    
    #total_charge_pC = mysettings['distgen:total_charge:value']
    #if total_charge_pC < MIN_CHARGE_pC:
    #    logger.info(f'total charge is too low: {total_charge_pC:.2f} pC, skipping')
    #    return dat
    
    outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=True )
    
    dat['outputs'] =  outputs   
    #logger.info(f'...finished in {(time()-t0):.1f} s')
    fname = fname=f'{SUMMARY_OUTPUT_DIR_DATED}/{PREFIX}-{itime}.json'

    json.dump(dat, open(fname, 'w'), cls=NpEncoder)
    #logger.info(f'Summary output written: {fname}')
    return dat


if __name__ == '__main__':
    while True:
        try:
            result = run1()
            sleep(10)
        except Exception as e:
            logger.info(e)
            if (e.__class__.__name__ == 'Exception'):
                logger.info('Stopping the Program')
                break
            else:
                logger.info('Something BAD happened. Sleeping for 10 s ...')      
                sleep(10)
