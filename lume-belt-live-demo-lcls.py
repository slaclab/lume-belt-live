
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

def find_linac_info(E: BELT, ele_name: str):
    c = 299_792_458.0
    for ele in E.input.lattice_lines:
        if ele.name == ele_name:
            freq = ele.frequency
            lamb = c/freq
            V = ele.length*ele.gradient
            phase_deg = ele.phase_deg
            return lamb, V, phase_deg
    print("element not found!")
    return


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
    BC1_energy = pg['mean_energy']

    #beam chirp after L2
    pg = P_L2
    x = pg.z
    y = (pg.energy - pg['mean_energy'])/pg['mean_energy']
    chirp_L2,_  = np.polyfit(x, y, 1)
    BC2_energy = pg['mean_energy']

    #bunch length after BC1
    BC1_sigma_t = P_BC1['sigma_t']
    BC1_bunch_length = BC1_sigma_t*np.sqrt(10)
    BC1_current = P_BC1.charge/BC1_bunch_length
    

    #bunch length after BC2
    BC2_sigma_t = P_BC2['sigma_t']
    BC2_bunch_length = BC2_sigma_t*np.sqrt(12)
    BC2_current = P_BC2.charge/BC2_bunch_length

    L1_lamb, L1_V, L1_phase_deg = find_linac_info(E, 'L1')
    L2_lamb, L2_V, L2_phase_deg = find_linac_info(E, 'L2')

    m['L1_chirp'] = chirp_L1
    m['L2_chirp'] = chirp_L2
    m['L1_lamb'] = L1_lamb
    m['L2_lamb'] = L2_lamb
    m['L1_V'] = L1_V
    m['L2_V'] = L2_V
    m['L1_phase_deg'] = L1_phase_deg
    m['L2_phase_deg'] = L2_phase_deg
    
    
    
    m['BC1_sigma_t'] = BC1_sigma_t
    m['BC1_bunch_length'] = BC1_bunch_length
    m['BC1_current'] = BC1_current
    m['BC1_energy'] = BC1_energy
    m['BC2_sigma_t'] = BC2_sigma_t
    m['BC2_bunch_length'] = BC2_bunch_length
    m['BC2_current'] = BC2_current
    m['BC2_energy'] = BC2_energy
    


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


def linac_phase_feedback_from_current(
    h_sim,
    R56,
    I_sim,
    I_meas,
    V,
    E,
    lambda_rf,
    phi_old,
    gain_h=0.3,
    max_step_deg=0.5,
    min_current=1e-12,
    A_min=0.02,
    A_safe=0.05,
    force_normal_side_near_full=True,
):
    """
    One-step linac phase feedback with protection near full compression.

    Convention:
        R56 > 0
        normal compression: A = 1 + R56*h > 0
        over compression:  A = 1 + R56*h < 0
        h < 0 for compression
        phi < 0 for compression
        h(phi) = (V*k_rf/E) * sin(phi)

    If |A_sim| < A_min and force_normal_side_near_full=True,
    the controller ignores current-error feedback and pushes the beam
    toward a safe normal-compression point A_target = A_safe > 0.
    """

    if R56 <= 0:
        raise ValueError("This function assumes R56 > 0.")

    if A_safe <= 0:
        raise ValueError("A_safe must be positive to force the normal-compression side.")

    if A_min <= 0:
        raise ValueError("A_min should be positive.")

    I_sim = max(I_sim, min_current)
    I_meas = max(I_meas, min_current)

    A_sim = 1.0 + R56 * h_sim

    if A_sim > 0:
        compression_branch = "normal_compression"
    elif A_sim < 0:
        compression_branch = "over_compression"
    else:
        compression_branch = "full_compression"
    print(compression_branch)
    near_full_compression = abs(A_sim) < A_min

    current_error_log = np.log(I_meas / I_sim)

    if near_full_compression and force_normal_side_near_full:
        print("near_full_compression")
        # Explicitly choose the normal-compression side.
        # This ignores current error because current cannot determine the branch near A=0.
        A_target = A_safe
        h_target = (A_target - 1.0) / R56

        delta_h_full = h_target - h_sim
        delta_h = gain_h * delta_h_full

        feedback_mode = "force_normal_side"

    else:
        # Standard log-current feedback.
        #
        # d ln I / dh = -R56 / A
        # delta_h = -A/R56 * ln(I_meas/I_sim)
        delta_h = -gain_h * A_sim / R56 * current_error_log

        h_target = h_sim + delta_h / max(gain_h, 1e-15)
        A_target = 1.0 + R56 * h_target

        feedback_mode = "log_current_feedback"

    h_new_est = h_sim + delta_h
    A_new_est = 1.0 + R56 * h_new_est

    k_rf = 2.0 * np.pi / lambda_rf

    # Your convention:
    # h = (V*k_rf/E) * sin(phi)
    dh_dphi = (V * k_rf / E) * np.cos(phi_old)

    if abs(dh_dphi) < 1e-12:
        raise ValueError("Phase-to-chirp sensitivity is too small.")

    delta_phi_raw = delta_h / dh_dphi

    max_step_rad = np.deg2rad(max_step_deg)
    delta_phi_applied = np.clip(delta_phi_raw, -max_step_rad, max_step_rad)

    phi_new = phi_old + delta_phi_applied

    diagnostics = {
        "feedback_mode": feedback_mode,
        "h_sim": h_sim,
        "R56": R56,
        "A_sim": A_sim,
        "A_target": A_target,
        "A_new_est": A_new_est,
        "h_target": h_target,
        "h_new_est": h_new_est,
        "compression_branch": compression_branch,
        "near_full_compression": near_full_compression,
        "A_min": A_min,
        "A_safe": A_safe,
        "I_sim": I_sim,
        "I_meas": I_meas,
        "current_error_log": current_error_log,
        "current_ratio_Imeas_over_Isim": I_meas / I_sim,
        "gain_h": gain_h,
        "delta_h": delta_h,
        "k_rf": k_rf,
        "dh_dphi": dh_dphi,
        "delta_phi_raw_rad": delta_phi_raw,
        "delta_phi_raw_deg": np.rad2deg(delta_phi_raw),
        "delta_phi_applied_rad": delta_phi_applied,
        "delta_phi_applied_deg": np.rad2deg(delta_phi_applied),
        "phi_old_rad": phi_old,
        "phi_old_deg": np.rad2deg(phi_old),
        "phi_new_rad": phi_new,
        "phi_new_deg": np.rad2deg(phi_new),
    }

    return np.rad2deg(phi_new), diagnostics


def run1_lcls(input_beam = "./example_lcls/from_Litrack_250pC.h5", input_lattice = "example_lcls/belt.in", force_normal_compression = True, Amin = 0.02):
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
    #print(mysettings)
    dat['isotime'] = itime
    
    # Record inputs
    dat['inputs'] = mysettings
    dat['config'] = CONFIG0
    dat['pv_mapping_dataframe'] = df.to_dict()
    

    t0 = time()
    
    
    #-------Initial Run-------------------------------------------------
    print("Initial Run")
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
    
    
    #print(mysettings)
    
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
    V = outputs['L1_V']
    E = outputs['BC1_energy']
    
    print("Third run, tune L1 phase to match BC1 current from ", BC1_current_sim, "A to ", BC1_current_live, "A")

    count  = 0
    while np.abs(BC1_current_live - BC1_current_sim)/BC1_current_live > 0.1:
        
        L1_new_phase_deg, diagnostics = linac_phase_feedback_from_current(h_sim = L1_chirp_sim, R56 = BC1_R56, I_sim = BC1_current_sim, I_meas = BC1_current_live,V = V, E = E, lambda_rf = outputs['L1_lamb'], phi_old = np.radians(outputs['L1_phase_deg']), gain_h=0.4, max_step_deg=1.5, min_current=1e-12, A_min=Amin, A_safe = 0.05, force_normal_side_near_full=force_normal_compression)
        
        print("Change L1 phase from ", mysettings['L1:phase_deg'], "deg to", L1_new_phase_deg, "deg")
        
    

        mysettings['EBC1:energy_increment'] += mysettings['L1_amplitude']*np.cos(mysettings['L1:phase_deg']/180*np.pi) - mysettings['L1_amplitude']*np.cos(L1_new_phase_deg/180*np.pi)
        mysettings['L1:phase_deg'] = L1_new_phase_deg

        outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime, prefix),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=False )

        BC1_current_sim = outputs['BC1_current']
        L1_chirp_sim = outputs['L1_chirp']

        print(f"The simulated BC1 current is {BC1_current_sim}")

    dat['outputs_run2'] =  outputs
    #-----Fourth run, tune L2 phase to match BC2 current----------------------------
    
    BC2_current_sim = outputs['BC2_current']
    L2_chirp_sim = outputs['L2_chirp']
    BC2_current_live = mysettings['BC2_current']

    BC2_angle = mysettings['BC2:angle']
    Ldb = 9.866942
    Lb = 5.491250000E-01

    BC2_R56 = 2*BC2_angle**2*(Ldb + 2/3*Lb)
    V = outputs['L2_V']
    E = outputs['BC2_energy']
    
    print("Fourth run, tune L2 phase to match BC2 current from ", BC2_current_sim, "A to ", BC2_current_live, "A")


    count = 0
    while (np.abs(BC2_current_live - BC2_current_sim)/BC2_current_live > 0.15) and count < 6:
        
    #calculate new L2 phase
    #r = BC2_current_live/BC2_current_sim
    #L2_new_chirp = ((1 - r) + BC2_R56*L2_chirp_sim)/(r*BC2_R56)

        L2_new_phase_deg, diagnostics = linac_phase_feedback_from_current(h_sim = L2_chirp_sim, R56 = BC2_R56, I_sim = BC2_current_sim, I_meas = BC2_current_live, V = V, E = E, lambda_rf = outputs['L2_lamb'], phi_old = np.radians(outputs['L2_phase_deg']), gain_h=0.6, max_step_deg=2.0, min_current=1e-12, A_min=Amin, A_safe = 0.05, force_normal_side_near_full=force_normal_compression)
        print("Change L2 phase from ", mysettings['L2:phase_deg'], "deg to", L2_new_phase_deg, "deg")
    

        mysettings['EBC2:energy_increment'] += mysettings['L2_amplitude']*np.cos(mysettings['L2:phase_deg']/180*np.pi) - mysettings['L2_amplitude']*np.cos(L2_new_phase_deg/180*np.pi)
        mysettings['L2:phase_deg'] = L2_new_phase_deg

        outputs = evaluate_belt(CONFIG0, mysettings,
                                       merit_f=lambda x: my_merit(x, itime, prefix),
                                       archive_path=ARCHIVE_DIR_DATED,
                                        verbose=False )
        BC2_current_sim = outputs['BC2_current']
        L2_chirp_sim = outputs['L2_chirp']
        print(f"The simulated BC2 current is {BC2_current_sim}")

        count += 1
        
        
    dat['outputs'] =  outputs   
    
    fname = fname=f'{SUMMARY_OUTPUT_DIR_DATED}/{prefix}-{itime}.json'

    json.dump(dat, open(fname, 'w'), cls=NpEncoder)
    #logger.info(f'Summary output written: {fname}')
    return dat




if __name__ == '__main__':
     result = run1_lcls()
    