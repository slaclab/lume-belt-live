#!/usr/bin/env python
# coding: utf-8

# # Dashboard creation routines

# In[1]:


#from belt import belt

import os
import json
import numpy as np

from pathlib import Path

import matplotlib as mpl
#mpl.use('Agg')
import matplotlib.pyplot as plt
plt.style.use('dark_background')
from belt.run import BELT

# In[2]:


from PIL import Image, ImageOps, ImageEnhance 

def fig2data ( fig ):
    """
    @brief Convert a Matplotlib figure to a 4D numpy array with RGBA channels and return it
    @param fig a matplotlib figure
    @return a numpy 3D array of RGBA values
    """
    # draw the renderer
    fig.canvas.draw ( )
    # Get the RGBA buffer from the figure
    w,h = fig.canvas.get_width_height()
    buf = np.frombuffer ( fig.canvas.tostring_argb(), dtype=np.uint8 )
    buf.shape = ( w, h, 4 )
    # canvas.tostring_argb give pixmap in ARGB mode. Roll the ALPHA channel to have it in RGBA mode
    buf = np.roll ( buf, 3, axis = 2 )
    return buf

def fig2img ( fig ):
    """
    @brief Convert a Matplotlib figure to a PIL Image in RGBA format and return it
    @param fig a matplotlib figure
    @return a Python Imaging Library ( PIL ) image
    """
    # put the figure pixmap into a numpy array
    buf = fig2data ( fig )
    w, h, d = buf.shape
    return Image.frombytes( "RGBA", ( w ,h ), buf.tobytes( ) )


# In[3]:


def iscreen(belt_object, file_id=201, k1='t', k2='energy', dpi=72, title=None):
    fig = belt_object.output.plot_distribution(file_id, k1, k2, bins = 100)
    fig.dpi=dpi
    
    if not title:
        title = file_id
    fig.axes[2].set_title(title)
    fig.tight_layout()
    return fig2img(fig)


# In[4]:


def info_str(belt_object, name=''):
    I = belt_object
    H = belt_object.input.parameters
    Pf = belt_object.output.particle_distributions[201]
    
    run_time = I.output.run.run_time

    
    summary=f"""{name} 
    
LUME-BELT running BELT 
Impact-T created particles after the injector
Particles in openPMD-beamphysics format 

{H.np:,} macroparticles
total charge: {Pf.charge*1e12:.1f} pC
Space charge grid:  {H.nz}
final kinetic energy: {I.output.stats.kinetic_energy[-1]/1e9:.3f} GeV
final bunch length: {I.output.stats.rms_z[-1]*1e6:.2f} um
final energy spread: {I.output.stats.rms_delta_gamma[-1]/1e2:.2f} %

Run time: {run_time:.1f} s

"""
    return summary

def itext(belt_object, dpi=72, name=''):
    text = info_str(belt_object, name=name)
    fig, ax = plt.subplots(figsize=(5,4))
    fig.dpi=dpi
    fig.tight_layout()
    ax.set_axis_off()
    ax.text(0.1, 0.5, text, fontsize=13, horizontalalignment='left', verticalalignment='center', transform=ax.transAxes)
    return fig2img(fig)


# In[5]:


def make_dashboard(belt_object=None,
                   dat=None,
                   itime=None,
                   outpath='test/',
                   file_id1=101,
                   file_id2= 211,
                   file_id3=213,
                   file_id4 =201,
                   name='lume-belt-live'
                  ):
    """
    Makes a composite dashboard image from data dict
    
    Returns the path to the figure written
    """
    if belt_object:
        I = belt_object   
    else:
        itime = dat['isotime']
        I = BELT.from_archive(dat['outputs']['archive'])
        #G = Generator()
        #G.load_archive(dat['archive'])
    #return I # Debug
    
    run_time = I.output.run.run_time
    # Main figure
    FIG0 = I.plot( return_figure=True)
    
    
    #n_particle = I.particles['final_particles'].n_particle
    
    title=f'Acquired settings at {itime}, simulation run time: {run_time:5.1f} s'
    
    FIG0.tight_layout()
    FIG0.axes[0].set_title(title)
    
    DPI = 150 # test
    FIG0.dpi=DPI
    im0 = fig2img(FIG0)
    
    
    # For short debugging runs
    #if screen1 not in I.particles:
    #    screen1='initial_particles'
    #    screen2='initial_particles'
    #    screen3='final_particles'

        
    # info text
    #imtext = ImageOps.invert(itext(I, dpi=DPI).convert('RGB'))     
    imtext =itext(I, dpi=DPI, name=name)

    im1 = iscreen(I, file_id=file_id1, dpi=DPI, title= "Initial")
    im2 = iscreen(I, file_id=file_id2, dpi=DPI, title = "After BC1")
    #im3 = iscreen(I, screen=screen3, k1='x', k2='y', dpi=DPI)
    im3 = imtext
    im4 = iscreen(I, file_id=file_id3, dpi=DPI, title = "After BC2")
    im5 = iscreen(I, file_id=file_id4, dpi=DPI, title = "Final")
    
    #im99 = iscreen(I, screen='initial_particles', k1='x', k2='y', dpi=DPI, title='cathode')
    
    SIZE =  (im1.width + im2.width + im4.width + im5.width, im0.height+im5.height)
    ii = Image.new('RGB', SIZE)
    
    invim0 = ImageOps.invert(im0.convert('RGB'))
    ii.paste(im0, (0, 10))
    
    #ii.paste(im99, (0, im0.height))
    ii.paste(im1, (0, im0.height))
    ii.paste(im2, (im1.width,im0.height))
    ii.paste(im3, (im0.width,0))
    ii.paste(im4, (im1.width + im2.width,im0.height))
    ii.paste(im5, (im1.width + im2.width + im4.width,im0.height))
    
    fname = f'{name}-{itime}-dashboard.png'
    fout = os.path.join(outpath, fname)
    
    # Enhance contrast
    #enhancer = ImageEnhance.Brightness(ii) 
    enhancer = ImageEnhance.Contrast(ii) 
    iout = enhancer.enhance(1.2)
    iout.save(fout)
    
    return fout
    


# In[7]:


#%%capture
#I0 = make_dashboard(dat=json.load(open('output/lume-impact-live-demo-2021-04-05T19:13:18-07:00.json')))

