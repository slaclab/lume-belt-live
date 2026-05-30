
from tools import isotime

from belt.tools import NpEncoder
import pandas as pd
import numpy as np

import h5py
import json
import epics

from belt.run import BELT

import sys
import os
import toml
from time import sleep, time
import datetime
from belt.evaluate import default_belt_merit
from belt.belt_impact import run_belt, evaluate_belt
from make_dashboard import make_dashboard
from pmd_beamphysics import ParticleGroup
import matplotlib.pyplot as plt
plt.style.use('default')


#Get PV list
CSV = 'pv_mapping/lcls_belt.csv'
DF = pd.read_csv(CSV)#.dropna()
PVLIST = list(DF['device_pv_name'].dropna()) 
MONITOR = {pvname:epics.PV(pvname) for pvname in PVLIST}


ARCHIVE_DIR = './archive'

SNAPSHOT_DIR = './snapshot'
SUMMARY_OUTPUT_DIR = './summary'
PLOT_OUTPUT_DIR = './plot'






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





def get_snapshot(snapshot_file=None):
    itime = isotime()
    pvdata =  {k:MONITOR[k].get() for k in MONITOR}
    
    
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

def get_settings(csv, base_settings={}, snapshot_dir=None, snapshot_file=None):
    """
    Fetches live settings for all devices in the CSV table, and translates them to simulation inputs
     
    """
    df = DF[DF['device_pv_name'].notna()]
    assert len(df) > 0, 'Empty dataframe!'
    
    pv_names = list(df['device_pv_name'])

    pvdata, itime, USE_VCC_LOCAL = get_snapshot(snapshot_file)
    
    df['pv_value'] = [pvdata[k] for k in pv_names]
    

    # Collect settings
    settings = base_settings.copy()


    HL_phase = df.loc[df["Variable"] == "L1X_phase", 'pv_value' ].values[0] 
    HL_amplitude = df.loc[df["Variable"] == "L1X_amplitude", 'pv_value' ].values[0]*1e6
    HL_gradient = HL_amplitude/0.5948

    
    L1_phase = df.loc[df["Variable"] == "L1B_phase", 'pv_value' ].values[0]
    L1_amplitude = df.loc[df["Variable"] == "L1B_amplitude", 'pv_value' ].values[0]*1e6
    L1_gradient = L1_amplitude/8.7825
                    

    L2_phase = df.loc[df["Variable"] == "L2B_phase", 'pv_value' ].values[0]
    L2_amplitude = df.loc[df["Variable"] == "L2B_amplitude", 'pv_value' ].values[0]*1e6
    L2_gradient = L2_amplitude/329.1

    
    L3_phase = df.loc[df["Variable"] == "L3B_phase", 'pv_value' ].values[0]
    L3_amplitude = df.loc[df["Variable"] == "L3B_amplitude", 'pv_value' ].values[0]*1e6
    L3_gradient = L3_amplitude/552.9 

    

    
    BC1_energy = df.loc[df["Variable"] == "BC1_energy", 'pv_value' ].values[0]
    #BC1_rigidity = (df.loc[df["Variable"] == "BCX11", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX12", 'pv_value' ].values[0] +
    #         df.loc[df["Variable"] == "BCX13", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX14", 'pv_value' ].values[0])/4/10
    #BC1_angle = BC1_rigidity/(3.3356*BC1_energy)
   

    BC2_energy = df.loc[df["Variable"] == "BC2_energy", 'pv_value' ].values[0]
    #BC2_rigidity = (df.loc[df["Variable"] == "BCX21", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX22", 'pv_value' ].values[0] +
    #         df.loc[df["Variable"] == "BCX23", 'pv_value' ].values[0] + df.loc[df["Variable"] == "BCX24", 'pv_value' ].values[0])/4/10
    #BC2_angle = BC2_rigidity/(3.3356*BC2_energy)
  


    BC1_rigidity = df.loc[df["Variable"] == "BX12", 'pv_value' ].values[0] /10
    BC1_angle = BC1_rigidity/(3.3356*BC1_energy)

    BC2_rigidity = df.loc[df["Variable"] == "BX21", 'pv_value' ].values[0]/10

    BC2_angle = BC2_rigidity/(3.3356*BC2_energy)


    initial_energy = df.loc[df["Variable"] == "DL1_energy", 'pv_value'].values[0]*1e9

    BC1_energy_increment =BC1_energy*1e9 - (initial_energy + L1_amplitude*np.cos(L1_phase/180*np.pi) +  HL_amplitude*np.cos(HL_phase/180*np.pi))
    #BC1_energy_increment =BC1_energy*1e9 - (90e6 + L1_energy*1e6 )
    BC2_energy_increment = BC2_energy*1e9 - (BC1_energy*1e9 + L2_amplitude*np.cos(L2_phase/180*np.pi))

    settings["BC1:angle"] = BC1_angle
    settings["BC2:angle"] = BC2_angle
    settings["L1_amplitude"] = L1_amplitude
    settings["L2_amplitude"] = L2_amplitude
    settings["L1:gradient"] = L1_gradient
    settings["L1:phase_deg"] = L1_phase #+ phase_shift
    settings["L2:gradient"] = L2_gradient
    settings["L2:phase_deg"] = L2_phase #+ phase_shift
    settings["L3:gradient"] = L3_gradient
    settings["L3:phase_deg"] = L3_phase 
    settings["HL:gradient"] = HL_gradient
    settings["HL:phase_deg"] = HL_phase 
    settings["EBC1:energy_increment"] = BC1_energy_increment
    settings["EBC2:energy_increment"] = BC2_energy_increment
    settings["Q0"] = df.loc[df["Variable"] == "Charge_inj", 'pv_value'].values[0]*1e-9
    settings["Q1"] = df.loc[df["Variable"] == "Charge_BC1", 'pv_value'].values[0]*1e-12
    settings["BC1_current"] = df.loc[df["Variable"] == "BC1_current", 'pv_value'].values[0]
    settings["BC2_current"] = df.loc[df["Variable"] == "BC2_current", 'pv_value'].values[0]

    #update initial particle and charge
    if "Impact_particles" in settings.keys():
        print("setting initial energy  = ", initial_energy/1e6, "MeV")
        print("setting initial charge  = ", settings["Q0"]*1e12, "pC")
        P = ParticleGroup(settings["Impact_particles"])
        P.pz += -P["mean_pz"] + initial_energy
        P.charge = settings["Q0"]
        new_particle_file = "./particles.h5"
        P.write(new_particle_file)
        settings["Impact_particles"] = new_particle_file
        
    
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
        filename = os.path.abspath(os.path.join(snapshot_dir, f'snapshot-{itime}.h5'))
    #    total_charge_pC = settings['distgen:total_charge:value']
    #    if total_charge_pC < MIN_CHARGE_pC:
    #        logger.info(f'total charge is too low: {total_charge_pC:.2f} pC, not saving snapshot')         
    #    else:
        save_pvdata(filename, pvdata, itime)
    #        logger.info(f'EPICS shapshot written: {filename}')
        
        
    return settings, df, itime

def convertToDatedFormat(destionation_folder):
    curr_date = datetime.date.today()
    year,month,day = curr_date.strftime('%Y'),curr_date.strftime('%m'),curr_date.strftime('%d')
    destionation_folder_dated = destionation_folder + "/" + year + "/" + month + "/" + day

    if not os.path.exists(destionation_folder_dated):
        os.makedirs(destionation_folder_dated)
    
    return destionation_folder_dated


def my_belt_merit(E: BELT):
    """
    merit function to operate on an evaluated LUME-BELT object E.

    Returns dict of scalar values
    """
    # Check for error
    if E.output.run.error:
        return {"error": True}
    else:
        m = {"error": False}

    # Gather stat output
    stats_keys = [
        "kinetic_energy",
        "gamma",
        "mean_z",
        "rms_z",
        "mean_delta_gamma",
        "rms_delta_gamma",
    ]
    for k in stats_keys:
        m["end_" + k] = getattr(E.output.stats, k)[-1]

    m["run_time"] = E.output.run.run_time

    #-----------------------------------------------------------#
    #         for RF phase feed back                            #
    #-----------------------------------------------------------#
    P_L1 = E.output.particle_distributions[109].to_particlegroup()
    P_BC1 = E.output.particle_distributions[113].to_particlegroup()
    P_L2 = E.output.particle_distributions[115].to_particlegroup()
    P_BC2 = E.output.particle_distributions[117].to_particlegroup()

    #beam chirp after L1
    pg = P_L1
    x = pg.z
    y = (pg.energy - pg['mean_energy'])/pg['mean_energy']
    chirp_L1,_  = np.polyfit(x, y, 1)

    #beam chirp after L2
    pg = P_L2
    x = pg.z
    y = (pg.energy - pg['mean_energy'])/pg['mean_energy']
    chirp_L2,_  = np.polyfit(x, y, 1)

    #bunch length after BC1
    BC1_sigma_t = P_BC1['sigma_t']
    BC1_bunch_length = BC1_sigma_t*np.sqrt(10)
    BC1_current = P_BC1.charge/BC1_bunch_length

    #bunch length after BC2
    BC2_sigma_t = P_BC2['sigma_t']
    BC2_bunch_length = BC2_sigma_t*np.sqrt(12)
    BC2_current = P_BC2.charge/BC2_bunch_length

    m['L1_chirp'] = chirp_L1
    m['L2_chirp'] = chirp_L2
    m['BC1_sigma_t'] = BC1_sigma_t
    m['BC1_bunch_length'] = BC1_bunch_length
    m['BC1_current'] = BC1_current
    m['BC2_sigma_t'] = BC2_sigma_t
    m['BC2_bunch_length'] = BC2_bunch_length
    m['BC2_current'] = BC2_current
    


    #--------------------------------------------------------------#
    P = E.output.particle_distributions[201].to_particlegroup()
    P_init = E.output.particle_distributions[101].to_particlegroup()

    # All impact particles read back have status==1
    #
    ntotal = len(P_init)
    nlost = ntotal - len(P)

    m["end_n_particle_loss"] = nlost

    # Get live only for stat calcs
    P = P.where(P.status == 1)

    # No live particles
    if len(P) == 0:
        return {"error": True}

    # Special
    m["end_total_charge"] = P["charge"]
    m["end_higher_order_energy_spread"] = P["higher_order_energy_spread"]

    # Remove annoying strings
    if "why_error" in m:
        m.pop("why_error")

    return m

def my_merit(belt_object, itime, prefix):
    # Collect standard output statistics
    merit0 = my_belt_merit(belt_object)
    
    PLOT_OUTPUT_DIR_DATED = convertToDatedFormat(PLOT_OUTPUT_DIR)
    #Overriding at runtime to save in dated folders
    dashboard_kwargs = {"outpath": PLOT_OUTPUT_DIR_DATED, "name": prefix}
    
    # Make the dashboard from the evaluated object
    plot_file = make_dashboard(belt_object, itime=itime, **dashboard_kwargs)
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

def run1_lcls(input_beam = "./example_lcls/from_Litrack_250pC.h5", input_lattice = "example_lcls/belt.in"):
    dat = {}
    prefix = 'lume-belt-live'
    SETTINGS0 = {"Impact_particles": input_beam}
    CONFIG0 = {"input": input_lattice, "workdir": os.environ.get("SCRATCH")}

    SNAPSHOT_DIR_DATED = convertToDatedFormat(SNAPSHOT_DIR)
    ARCHIVE_DIR_DATED = convertToDatedFormat(ARCHIVE_DIR)
    SUMMARY_OUTPUT_DIR_DATED = convertToDatedFormat(SUMMARY_OUTPUT_DIR)
        
    # Acquire settings
    mysettings, df,  itime = get_settings(CSV,
                                                           SETTINGS0,
                                                           snapshot_dir=SNAPSHOT_DIR_DATED,
                                                          snapshot_file=None)        
    print(mysettings)
    dat['isotime'] = itime
    
    # Record inputs
    dat['inputs'] = mysettings
    dat['config'] = CONFIG0
    dat['pv_mapping_dataframe'] = df.to_dict()
    

    t0 = time()
    
    
    #-------Initial Run-------------------------------------------------
    outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime, prefix),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=False )

    dat['outputs_run0'] =  outputs  

    #-------Second Run, tweak BC1 collimator to get the right charge----------------
    run1_out = BELT.from_archive(outputs['archive'])
    #particles after BC1
    pg = run1_out.output.particle_distributions[111]
    z = pg.z
    # set collimator to cut the right charge
    Q0 = mysettings["Q0"]
    Q1 = mysettings["Q1"]

    print("Second run, cutting beam charge from ", Q0*1e12, "pC to ", Q1*1e12, "pC")
    
    f = Q1 / Q0
    alpha = 0.5 * (1 - f)
    zmin = np.quantile(z, alpha)
    zmax = np.quantile(z, 1 - alpha)
    mysettings["BC1_Col:zmin"] = zmin
    mysettings["BC1_Col:zmax"] = zmax
    
    
    print(mysettings)
    
    outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime, prefix),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=False )
    
    dat['outputs_run1'] =  outputs
    #-----Thrid run, tune L1 phase to match BC1 current----------------------------
    
    BC1_current_sim = outputs['BC1_current']
    L1_chirp_sim = outputs['L1_chirp']
    BC1_current_live = mysettings['BC1_current']

    BC1_angle = mysettings['BC1:angle']
    Ldb = 2.4456
    Lb = 2.034970000e-01

    BC1_R56 = 2*BC1_angle**2*(Ldb + 2/3*Lb)
    print("Third run, tune L1 phase to match BC1 current from ", BC1_current_sim, "A to ", BC1_current_live, "A")
    print(f'BC1_R56 = {BC1_R56}')
    while np.abs(BC1_current_live - BC1_current_sim)/BC1_current_live > 0.1:
        
        #calculate new L1 phase
        #r = BC1_current_live/BC1_current_sim
        #L1_new_chirp = ((1 - r) + BC1_R56*L1_chirp_sim)/(r*BC1_R56)
        if BC1_R56*L1_chirp_sim + 1 < 0:
            print("BC1 Overcompression")
        L1_new_chirp = (BC1_current_sim/BC1_current_live*np.abs(1 + BC1_R56*L1_chirp_sim) - 1)/BC1_R56

        
        sin_L1_phase = L1_new_chirp/L1_chirp_sim*np.sin(mysettings['L1:phase_deg']/180*np.pi)
        L1_new_phase = np.asin(sin_L1_phase)
        L1_new_phase_deg = L1_new_phase/np.pi*180
        
        print("Change L1 phase from ", mysettings['L1:phase_deg'], "deg to", L1_new_phase_deg, "deg")
    

        mysettings['EBC1:energy_increment'] += mysettings['L1_amplitude']*np.cos(mysettings['L1:phase_deg']/180*np.pi) - mysettings['L1_amplitude']*np.cos(L1_new_phase_deg/180*np.pi)
        mysettings['L1:phase_deg'] = L1_new_phase_deg

        outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime, prefix),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=False )

        BC1_current_sim = outputs['BC1_current']
        L1_chirp_sim = outputs['L1_chirp']

    dat['outputs_run2'] =  outputs
    #-----Fourth run, tune L2 phase to match BC2 current----------------------------
    
    BC2_current_sim = outputs['BC2_current']
    L2_chirp_sim = outputs['L2_chirp']
    BC2_current_live = mysettings['BC2_current']

    BC2_angle = mysettings['BC2:angle']
    Ldb = 9.866942
    Lb = 5.491250000E-01

    BC2_R56 = 2*BC2_angle**2*(Ldb + 2/3*Lb)
    print("Fourth run, tune L2 phase to match BC2 current from ", BC2_current_sim, "A to ", BC2_current_live, "A")

    
    while np.abs(BC2_current_live - BC2_current_sim)/BC2_current_live > 0.15:
        
    #calculate new L2 phase
    #r = BC2_current_live/BC2_current_sim
    #L2_new_chirp = ((1 - r) + BC2_R56*L2_chirp_sim)/(r*BC2_R56)

        if BC2_R56*L2_chirp_sim + 1 < 0:
            print("BC2 Overcompression")
        L2_new_chirp = (BC2_current_sim/BC2_current_live*np.abs(1 + BC2_R56*L2_chirp_sim) - 1)/BC2_R56
        
   
        sin_L2_phase = L2_new_chirp/L2_chirp_sim*np.sin(mysettings['L2:phase_deg']/180*np.pi)
        L2_new_phase = np.asin(sin_L2_phase)
        L2_new_phase_deg = L2_new_phase/np.pi*180
        print("Change L2 phase from ", mysettings['L2:phase_deg'], "deg to", L2_new_phase_deg, "deg")
    

        mysettings['EBC2:energy_increment'] += mysettings['L2_amplitude']*np.cos(mysettings['L2:phase_deg']/180*np.pi) - mysettings['L2_amplitude']*np.cos(L2_new_phase_deg/180*np.pi)
        mysettings['L2:phase_deg'] = L2_new_phase_deg

        outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime, prefix),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=False )
        BC2_current_sim = outputs['BC2_current']
        L2_chirp_sim = outputs['L2_chirp']
        
        
    dat['outputs'] =  outputs   
    
    fname = fname=f'{SUMMARY_OUTPUT_DIR_DATED}/{prefix}-{itime}.json'

    json.dump(dat, open(fname, 'w'), cls=NpEncoder)
    #logger.info(f'Summary output written: {fname}')
    return dat




if __name__ == '__main__':
     result = run1_lcls()
    