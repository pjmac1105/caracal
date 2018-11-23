import sys
import os
import warnings
import stimela.dismissable as sdm
import astropy
from astropy.io import fits
import meerkathi
# Modules useful to calculate common barycentric frequency grid
from astropy.time import Time
from astropy.coordinates import SkyCoord
from astropy.coordinates import EarthLocation
from astropy import constants
import re, datetime
import numpy as np
import yaml

def freq_to_vel(filename):
    C = 2.99792458e+8 # m/s
    HI = 1.4204057517667e+9 # Hz
    filename=filename.split(':')
    filename='{0:s}/{1:s}'.format(filename[1],filename[0])
    if not os.path.exists(filename): meerkathi.log.info('Skipping conversion for {0:s}. File does not exist.'.format(filename))
    else:
        with fits.open(filename, mode='update') as cube:
            headcube = cube[0].header
            if 'restfreq' in headcube: restfreq = float(headcube['restfreq'])
            else: restfreq = HI
            if 'FREQ' in headcube['ctype3']:
                headcube['cdelt3'] = -C * float(headcube['cdelt3'])/restfreq
                headcube['crval3'] =  C * (1-float(headcube['crval3'])/restfreq)
                headcube['ctype3'] = 'VELO-HEL'
                if 'cunit3' in headcube: del headcube['cunit3']
            else: meerkathi.log.info('Skipping conversion for {0:s}. Input cube not in frequency.'.format(filename))

NAME = 'Make HI Cube'
def worker(pipeline, recipe, config):
    mslist = ['{0:s}-{1:s}.ms'.format(did, config['label']) for did in pipeline.dataid]
    pipeline.prefixes = ['meerkathi-{0:s}-{1:s}'.format(did,config['label']) for did in pipeline.dataid]
    prefixes = pipeline.prefixes
    restfreq = config.get('restfreq','1.420405752GHz')
    npix = config.get('npix', [1024])
    if len(npix) == 1:
        npix = [npix[0],npix[0]]
    cell = config.get('cell', 7)
    weight = config.get('weight', 'natural')
    robust = config.get('robust', 0)

    # Upate pipeline attributes (useful if, e.g., channel averaging was performed by the split_data worker)
    for i, prefix in enumerate(prefixes):
        msinfo = '{0:s}/{1:s}-obsinfo.json'.format(pipeline.output, prefix)
        meerkathi.log.info('Updating info from {0:s}'.format(msinfo))
        with open(msinfo, 'r') as stdr:
            spw = yaml.load(stdr)['SPW']['NUM_CHAN']
            pipeline.nchans[i] = spw
        meerkathi.log.info('MS has {0:d} spectral windows, with NCHAN={1:s}'.format(len(spw), ','.join(map(str, spw))))

        # Get first chan, last chan, chan width
        with open(msinfo, 'r') as stdr:
            chfr = yaml.load(stdr)['SPW']['CHAN_FREQ']
            firstchanfreq = [ss[0] for ss in chfr]
            lastchanfreq  = [ss[-1] for ss in chfr]
            chanwidth     = [(ss[-1]-ss[0])/(len(ss)-1) for ss in chfr]
            pipeline.firstchanfreq[i] = firstchanfreq
            pipeline.lastchanfreq[i]  = lastchanfreq
            pipeline.chanwidth[i] = chanwidth
            meerkathi.log.info('CHAN_FREQ from {0:s} Hz to {1:s} Hz with average channel width of {2:s} Hz'.format(','.join(map(str,firstchanfreq)),','.join(map(str,lastchanfreq)),','.join(map(str,chanwidth))))

    # Find common barycentric frequency grid for all input .MS, or set it as requested in the config file
    if pipeline.enable_task(config, 'mstransform') and config['mstransform'].get('doppler', True) and config['mstransform'].get('outchangrid', 'auto')=='auto':
        firstchanfreq=pipeline.firstchanfreq
        chanw=pipeline.chanwidth
        lastchanfreq=pipeline.lastchanfreq
        RA=pipeline.TRA
        Dec=pipeline.TDec
        teldict={
            'meerkat':[21.4430,-30.7130],
            'gmrt':[73.9723, 19.1174],
            'vla':[-107.6183633,34.0783584],
            'wsrt':[52.908829698,6.601997592],
            'atca':[-30.307665436,149.550164466],
            'askap':[116.5333,-16.9833],
                }
        tellocation=teldict[config.get('telescope','meerkat')]
        telloc=EarthLocation.from_geodetic(tellocation[0],tellocation[1])
        firstchanfreq_dopp,chanw_dopp,lastchanfreq_dopp = firstchanfreq,chanw,lastchanfreq
        for i, prefix in enumerate(prefixes):
            msinfo = '{0:s}/{1:s}-obsinfo.txt'.format(pipeline.output, prefix)
            with open(msinfo, 'r') as searchfile:
                for longdatexp in searchfile:
                   if "Observed from" in longdatexp:
                       dates   = longdatexp
                       matches = re.findall('(\d{2}[- ](\d{2}|January|Jan|February|Feb|March|Mar|April|Apr|May|May|June|Jun|July|Jul|August|Aug|September|Sep|October|Oct|November|Nov|Decem    ber|Dec)[\- ]\d{2,4})', dates)
                       obsstartdate = str(matches[0][0])
                       obsdate = datetime.datetime.strptime(obsstartdate, '%d-%b-%Y').strftime('%Y-%m-%d')
                       targetpos = SkyCoord(RA[i], Dec[i], frame='icrs', unit='deg')
                       v=targetpos.radial_velocity_correction(kind='barycentric',obstime=Time(obsdate), location=telloc).to('km/s')
                       corr=np.sqrt((constants.c-v)/(constants.c+v))
                       firstchanfreq_dopp[i], chanw_dopp[i], lastchanfreq_dopp[i] = firstchanfreq_dopp[i]*corr, chanw_dopp[i]*corr, lastchanfreq_dopp[i]*corr  #Hz, Hz, Hz
        # WARNING: the following line assumes a single SPW for the HI line data being processed by this worker!
        comfreq0,comfreql,comchanw = np.max(firstchanfreq_dopp), np.min(lastchanfreq_dopp), np.max(chanw_dopp)
        comfreq0+=comchanw # safety measure to avoid wrong Doppler settings due to change of Doppler correction during a day
        comfreql-=comchanw # safety measure to avoid wrong Doppler settings due to change of Doppler correction during a day
        nchan_dopp=int(np.floor(((comfreql - comfreq0)/comchanw)))+1
        comfreq0='{0:.3f}Hz'.format(comfreq0)
        comchanw='{0:.3f}Hz'.format(comchanw)
        meerkathi.log.info('Calculated common Doppler-corrected channel grid for all input .MS: {0:d} channels starting at {1:s} and with channel width {2:s}.'.format(nchan_dopp,comfreq0,comchanw))

    elif pipeline.enable_task(config, 'mstransform') and config['mstransform'].get('doppler', True) and config['mstransform'].get('outchangrid', 'auto')!='auto':
        if len(config['mstransform']['outchangrid'].split(','))!=3:
            meerkathi.log.error('Wrong format for mstransform:outchangrid in the .yml config file.')
            meerkathi.log.error('Current setting is mstransform:outchangrid:"{0:s}"'.format(config['mstransform']['outchangrid']))
            meerkathi.log.error('It must be "nchan,chan0,chanw" (note the commas) where nchan is an integer, and chan0 and chanw must include units appropriate for the chosen mstransform:mode')
            sys.exit(1)
        nchan_dopp,comfreq0,comchanw=config['mstransform']['outchangrid'].split(',')
        nchan_dopp=int(nchan_dopp)
        meerkathi.log.info('Set requested Doppler-corrected channel grid for all input .MS: {0:d} channels starting at {1:s} and with channel width {2:s}.'.format(nchan_dopp,comfreq0,comchanw))

    elif pipeline.enable_task(config, 'mstransform'):
        nchan_dopp,comfreq0,comchanw=None,None,None

    for i, msname in enumerate(mslist):
        prefix = '{0:s}_{1:d}'.format(pipeline.prefix, i)
        msname_mst=msname.replace('.ms','_mst.ms')

        if pipeline.enable_task(config, 'subtractmodelcol'):
            step = 'modelsub_{:d}'.format(i)
            recipe.add('cab/msutils', step,
                {
                    "command"  : 'sumcols',
                    "msname"   : msname,
                    "subtract" : True,
                    "col1"     : 'CORRECTED_DATA',
                    "col2"     : 'MODEL_DATA',
                    "column"   : 'CORRECTED_DATA'
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Subtract model column'.format(step))


        if pipeline.enable_task(config, 'mstransform'):
            if os.path.exists('{1:s}/{0:s}'.format(msname_mst,pipeline.msdir)): os.system('rm -r {1:s}/{0:s}'.format(msname_mst,pipeline.msdir))
            col=config['mstransform'].get('column', 'corrected')
            step = 'mstransform_{:d}'.format(i)
            recipe.add('cab/casa_mstransform', step,
                {
                    "msname"    : msname,
                    "outputvis" : msname_mst,
                    "regridms"  : config['mstransform'].get('doppler', True),
                    "mode"      : config['mstransform'].get('mode', 'frequency'),
                    "nchan"     : sdm.dismissable(nchan_dopp),
                    "start"     : sdm.dismissable(comfreq0),
                    "width"     : sdm.dismissable(comchanw),
                    "interpolation" : 'nearest',
                    "datacolumn" : col,
                    "restfreq"  :restfreq,
                    "outframe"  : config['mstransform'].get('outframe', 'bary'),
                    "veltype"   : config['mstransform'].get('veltype', 'radio'),
                    "douvcontsub" : config['mstransform'].get('uvlin', False),
                    "fitspw"    : sdm.dismissable(config['mstransform'].get('fitspw', None)),
                    "fitorder"  : config['mstransform'].get('fitorder', 1),
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Doppler tracking corrections'.format(step))


        if pipeline.enable_task(config, 'sunblocker'):
            if config['sunblocker'].get('use_mstransform',True): msnamesb = msname_mst
            else: msnamesb = msname
            step = 'sunblocker_{0:d}'.format(i)
            recipe.add("cab/sunblocker", step, 
                {
                    "command"   : "phazer",
                    "inset"     : msnamesb,
                    "outset"    : msnamesb,
                    "imsize"    : config['sunblocker'].get('imsize', max(npix)),
                    "cell"      : config['sunblocker'].get('cell', cell),
                    "pol"       : 'i',
                    "threshold" : config['sunblocker'].get('threshold', 4),
                    "mode"      : 'all',
                    "radrange"  : 0,
                    "angle"     : 0,
                    "show"      : prefix + '.sunblocker.pdf',
                    "verb"      : True,
                    "dryrun"    : False,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Block out sun'.format(step)) 

    if pipeline.enable_task(config, 'wsclean_image'):
        if config['wsclean_image'].get('use_mstransform',True):
            mslist = ['{0:s}-{1:s}_mst.ms'.format(did, config['label']) for did in pipeline.dataid]
        else:
            mslist = ['{0:s}-{1:s}.ms'.format(did, config['label']) for did in pipeline.dataid]
        spwid = config['wsclean_image'].get('spwid', 0)
        nchans = config['wsclean_image'].get('nchans',0)
        if nchans == 0: nchans = 'all'
        if nchans=='all':
          if config['wsclean_image']['use_mstransform']: nchans=nchan_dopp
          else: nchans=pipeline.nchans[0][spwid]
        firstchan = config['wsclean_image'].get('firstchan', 0)
        binchans  = config['wsclean_image'].get('binchans', 1)   
        channelrange = [firstchan, firstchan+nchans*binchans]
        # Construct weight specification
        if config['wsclean_image'].get('weight', 'natural') == 'briggs':
            weight = 'briggs {0:.3f}'.format( config['wsclean_image'].get('robust', robust))
        else:
            weight = config['wsclean_image'].get('weight', weight)


        wscl_niter = config['wsclean_image'].get('wscl_niter', 2)
        if wscl_niter ==0:
         step = 'wsclean_image_HI_no_loop'
         ownHIclean_mask=config['wsclean_image'].get('ownfitsmask',) 
         recipe.add('cab/wsclean', step,
               {                       
                  "msname"    : mslist,
                  "prefix"    : pipeline.prefix+'_HI',
                  "weight"    : weight,
                  "taper-gaussian" : str(config['wsclean_image'].get('taper',0)),
                  "pol"        : config['wsclean_image'].get('pol','I'),
                  "npix"      : config['wsclean_image'].get('npix', npix),
                  "padding"   : config['wsclean_image'].get('padding', 1.2),
                  "scale"     : config['wsclean_image'].get('cell', cell),
                  "channelsout"     : nchans,
                  "channelrange" : channelrange,
                  "niter"     : config['wsclean_image'].get('niter', 1000000),
                  "mgain"     : config['wsclean_image'].get('mgain', 1.0),
                  "auto-threshold"  : config['wsclean_image'].get('autothreshold', 5),
                  "fitsmask"  : ownHIclean_mask,
                  "auto-mask"  :   config['wsclean_image'].get('automask', 3),
                  "no-update-model-required": config['wsclean_image'].get('no_update_mod', True)
               },  
         input=pipeline.input,
         output=pipeline.output,
         label='{:s}:: Image HI'.format(step))

         if config['wsclean_image']['make_cube']:
             if not config['wsclean_image'].get('niter', 1000000): imagetype=['image','dirty']
             else:
                 imagetype=['image','dirty','psf','residual','model']
                 if config['wsclean_image'].get('mgain', 1.0)<1.0: imagetype.append('first-residual')
             for mm in imagetype:
                 step = 'make_{0:s}_cube'.format(mm.replace('-','_'))
                 recipe.add('cab/fitstool', step,
                     {    
                        "image"    : [pipeline.prefix+'_HI-{0:04d}-{1:s}.fits:output'.format(d,mm) for d in xrange(nchans)],
                        "output"   : pipeline.prefix+'_HI.{0:s}.fits'.format(mm),
                        "stack"    : True,
                        "delete-files" : True,
                        "fits-axis": 'FREQ',
                     },
                 input=pipeline.input,
                 output=pipeline.output,
                 label='{0:s}:: Make {1:s} cube from wsclean {1:s} channels'.format(step,mm.replace('-','_')))

         if pipeline.enable_task(config,'freq_to_vel'):
             for ss in ['dirty','psf','residual','model','image']:
                 cubename=pipeline.prefix+'_HI.'+ss+'.fits:output'
                 recipe.add(freq_to_vel, 'spectral_header_to_vel_radio_{0:s}_cube'.format(ss),
                            {
                           'filename' : cubename,
                            },
                            input=pipeline.input,
                            output=pipeline.output,
                            label='Convert spectral axis from frequency to radio velocity for cube {0:s}'.format(cubename))       

        else:
           for j in range(wscl_niter):            
            if j==0:
             step = 'wsclean_image_HI_loop_'+str(j)
             recipe.add('cab/wsclean', step,
                   {                       
                  "msname"    : mslist,
                  "prefix"    : pipeline.prefix+'_HI_'+str(j),
                  "weight"    : weight,
                  "taper-gaussian" : str(config['wsclean_image'].get('taper',0)),
                  "pol"        : config['wsclean_image'].get('pol','I'),
                  "npix"      : config['wsclean_image'].get('npix', npix),
                  "padding"   : config['wsclean_image'].get('padding', 1.2),
                  "scale"     : config['wsclean_image'].get('cell', cell),
                  "channelsout"     : nchans,
                  "channelrange" : channelrange,
                  "niter"     : config['wsclean_image'].get('niter', 1000000),
                  "mgain"     : config['wsclean_image'].get('mgain', 1.0),
                  "auto-threshold"  : config['wsclean_image'].get('autothreshold', 5),
                  "auto-mask"  :   config['wsclean_image'].get('automask', 3),
                  "no-update-model-required": config['wsclean_image'].get('no_update_mod', True)
                   },  
             input=pipeline.input,
             output=pipeline.output,
             label='{:s}:: Image HI'.format(step))

             if config['wsclean_image']['make_cube']:
                if not config['wsclean_image'].get('niter', 1000000): imagetype=['image','dirty']
                else:
                    imagetype=['image','dirty','psf','residual','model']
                    if config['wsclean_image'].get('mgain', 1.0)<1.0: imagetype.append('first-residual')
                for mm in imagetype:
                    step = 'make_{0:s}_cube'.format(mm.replace('-','_'))
                    recipe.add('cab/fitstool', step,
                        {    
                        "image"    : [pipeline.prefix+'_HI_'+str(j)+'-{0:04d}-{1:s}.fits:output'.format(d,mm) for d in xrange(nchans)],
                        "output"   : pipeline.prefix+'_HI_'+str(j)+'.{0:s}.fits'.format(mm),
                        "stack"    : True,
                        "delete-files" : True,
                        "fits-axis": 'FREQ',
                        },
                    input=pipeline.input,
                    output=pipeline.output,
                    label='{0:s}:: Make {1:s} cube from wsclean {1:s} channels'.format(step,mm.replace('-','_')))

            else:
                step = 'make_sofia_mask_'+str(j)
                cubename = pipeline.prefix+'_HI_'+str(j-1)+'.image.fits:output'
                outmask = pipeline.prefix+'_HI_'+str(j-1)+'.image_clean'
                recipe.add('cab/sofia', step,
                    {
                "import.inFile"         : cubename,
                "steps.doFlag"          : False,
                "steps.doScaleNoise"    : True,
                "steps.doSCfind"        : True,
                "steps.doMerge"         : True,
                "steps.doReliability"   : False,
                "steps.doParameterise"  : False,
       	        "steps.doWriteMask"     : True,
                "steps.doMom0"          : False,
                "steps.doMom1"          : False,
                "steps.doWriteCat"      : False,
                "flag.regions"          : [], 
                "scaleNoise.statistic"  : 'mad' ,
                "SCfind.threshold"      : 4, 
                "SCfind.rmsMode"        : 'mad',
                "merge.radiusX"         : 2, 
                "merge.radiusY"         : 2,
                "merge.radiusZ"         : 2,
                "merge.minSizeX"        : 2,
                "merge.minSizeY"        : 2, 
                "merge.minSizeZ"        : 2,
                "writeCat.basename"     : outmask,
                    },
                    input=pipeline.input,
                    output=pipeline.output,
                    label='{0:s}:: Make SoFiA mask'.format(step))


                HIclean_mask=pipeline.prefix+'_HI_'+str(j-1)+'.image_clean_mask.fits:output' 
                step = 'wsclean_image_HI_loop_'+str(j)
                recipe.add('cab/wsclean', step,
                    {                       
                  "msname"    : mslist,
                  "prefix"    : pipeline.prefix+'_HI_'+str(j),
                  "weight"    : weight,
                  "taper-gaussian" : str(config['wsclean_image'].get('taper',0)),
                  "pol"        : config['wsclean_image'].get('pol','I'),
                  "npix"      : config['wsclean_image'].get('npix', npix),
                  "padding"   : config['wsclean_image'].get('padding', 1.2),
                  "scale"     : config['wsclean_image'].get('cell', cell),
                  "channelsout"     : nchans,
                  "channelrange" : channelrange,
                  "fitsmask"  : HIclean_mask, 
                  "niter"     : config['wsclean_image'].get('niter', 1000000),
                  "mgain"     : config['wsclean_image'].get('mgain', 1.0),
                  "auto-threshold"  : config['wsclean_image'].get('autothreshold', 5),
                  "no-update-model-required": config['wsclean_image'].get('no_update_mod', True)
                    },  
                input=pipeline.input,
                output=pipeline.output,
                label='{:s}:: re-Image HI_'+str(j).format(step))


                if config['wsclean_image']['make_cube']:
                   if not config['wsclean_image'].get('niter', 1000000): imagetype=['image','dirty']
                   else:
                       imagetype=['image','dirty','psf','residual','model']
                       if config['wsclean_image'].get('mgain', 1.0)<1.0: imagetype.append('first-residual')
                   for mm in imagetype:
                       step = 'make_{0:s}_cube'.format(mm.replace('-','_'))
                       recipe.add('cab/fitstool', step,
                           {    
                        "image"    : [pipeline.prefix+'_HI_'+str(j)+'-{0:04d}-{1:s}.fits:output'.format(d,mm) for d in xrange(nchans)],
                        "output"   : pipeline.prefix+'_HI_'+str(j)+'.{0:s}.fits'.format(mm),
                        "stack"    : True,
                        "delete-files" : True,
                        "fits-axis": 'FREQ',
                           },
                       input=pipeline.input,
                       output=pipeline.output,
                       label='{0:s}:: Make {1:s} cube from wsclean {1:s} channels'.format(step,mm.replace('-','_')))


                if j==(wscl_niter-1):
                   step = 'make_sofia_mask_'+str(j)
                   cubename = pipeline.prefix+'_HI_'+str(j-1)+'.image.fits:output'
                   outmask = pipeline.prefix+'_HI.image_clean'
                   recipe.add('cab/sofia', step,
                       {
                "import.inFile"         : cubename,
                "steps.doFlag"          : False,
                "steps.doScaleNoise"    : True,
                "steps.doSCfind"        : True,
                "steps.doMerge"         : True,
                "steps.doReliability"   : False,
                "steps.doParameterise"  : False,
       	        "steps.doWriteMask"     : True,
                "steps.doMom0"          : False,
                "steps.doMom1"          : False,
                "steps.doWriteCat"      : False,
                "flag.regions"          : [], 
                "scaleNoise.statistic"  : 'mad' ,
                "SCfind.threshold"      : 4, 
                "SCfind.rmsMode"        : 'mad',
                "merge.radiusX"         : 2, 
                "merge.radiusY"         : 2,
                "merge.radiusZ"         : 2,
                "merge.minSizeX"        : 2,
                "merge.minSizeY"        : 2, 
                "merge.minSizeZ"        : 2,
                "writeCat.basename"     : outmask,
                       },
                       input=pipeline.input,
                       output=pipeline.output,
                       label='{0:s}:: Make SoFiA mask'.format(step))

               
                   HIclean_mask=pipeline.prefix+'_HI.image_clean_mask.fits:output' 
                   step = 'Running_final_wsclean_image_HI_loop'
                   recipe.add('cab/wsclean', step,
                       {                       
                  "msname"    : mslist,
                  "prefix"    : pipeline.prefix+'_HI',
                  "weight"    : weight,
                  "taper-gaussian" : str(config['wsclean_image'].get('taper',0)),
                  "pol"        : config['wsclean_image'].get('pol','I'),
                  "npix"      : config['wsclean_image'].get('npix', npix),
                  "padding"   : config['wsclean_image'].get('padding', 1.2),
                  "scale"     : config['wsclean_image'].get('cell', cell),
                  "channelsout"     : nchans,
                  "channelrange" : channelrange,
                  "fitsmask"  : HIclean_mask, 
                  "niter"     : config['wsclean_image'].get('niter', 1000000),
                  "mgain"     : config['wsclean_image'].get('mgain', 1.0),
                  "auto-threshold"  : config['wsclean_image'].get('autothreshold', 5),
                  "no-update-model-required": config['wsclean_image'].get('no_update_mod', True)
                       },  
                   input=pipeline.input,
                   output=pipeline.output,
                   label='{:s}:: re-Image HI_'+str(j).format(step))


                   if config['wsclean_image']['make_cube']:
                      if not config['wsclean_image'].get('niter', 1000000): imagetype=['image','dirty']
                      else:
                          imagetype=['image','dirty','psf','residual','model']
                          if config['wsclean_image'].get('mgain', 1.0)<1.0: imagetype.append('first-residual')
                      for mm in imagetype:
                          step = 'make_{0:s}_cube'.format(mm.replace('-','_'))
                          recipe.add('cab/fitstool', step,
                              {    
                        "image"    : [pipeline.prefix+'_HI-{0:04d}-{1:s}.fits:output'.format(d,mm) for d in xrange(nchans)],
                        "output"   : pipeline.prefix+'_HI.{0:s}.fits'.format(mm),
                        "stack"    : True,
                        "delete-files" : True,
                        "fits-axis": 'FREQ',
                              },
                          input=pipeline.input,
                          output=pipeline.output,
                          label='{0:s}:: Make {1:s} cube from wsclean {1:s} channels'.format(step,mm.replace('-','_')))

                   if pipeline.enable_task(config,'freq_to_vel'):
                       for ss in ['dirty','psf','residual','model','image']:
                            cubename=pipeline.prefix+'_HI.'+ss+'.fits:output'
                            recipe.add(freq_to_vel, 'spectral_header_to_vel_radio_{0:s}_cube'.format(ss),
                                       {
                           'filename' : cubename,
                                       },
                                       input=pipeline.input,
                                       output=pipeline.output,
                                       label='Convert spectral axis from frequency to radio velocity for cube {0:s}'.format(cubename))       

           for j in range(wscl_niter): 

                      if pipeline.enable_task(config,'freq_to_vel'):
                          for ss in ['dirty','psf','residual','model','image']:
                               cubename=pipeline.prefix+'_HI_'+str(j)+'.'+ss+'.fits:'+pipeline.output
                               recipe.add(freq_to_vel, 'spectral_header_to_vel_radio_{0:s}_cube'.format(ss),
                                          {
                           'filename' : cubename,
                                          },
                                          input=pipeline.input,
                                          output=pipeline.output,
                                          label='Convert spectral axis from frequency to radio velocity for cube {0:s}'.format(cubename))
                     
                                            
           for j in range(wscl_niter):
              if config['wsclean_image'].get('rm_intcubes',True):
                for ss in ['dirty','psf','residual','model','image']:
                  cubename=os.path.join(pipeline.output,pipeline.prefix+'_HI_'+str(j)+'.'+ss+'.fits')
                  HIclean_mask=os.path.join(pipeline.output,pipeline.prefix+'_HI_'+str(j)+'.image_clean_mask.fits')
                  if os.path.exists(cubename):
                   os.remove(cubename)
                  if os.path.exists(HIclean_mask):                
                   os.remove(HIclean_mask)



    if pipeline.enable_task(config, 'casa_image'):
        if config['casa_image']['use_mstransform']:
            mslist = ['{0:s}-{1:s}_mst.ms'.format(did, config['label']) for did in pipeline.dataid]
        step = 'casa_image_HI'
        spwid = config['casa_image'].get('spwid', 0)
        nchans = config['casa_image'].get('nchans', 0)
        if nchans == 0:
            nchans=pipeline.nchans[0][spwid]
        recipe.add('cab/casa_clean', step,
            {
                 "msname"         :    mslist,
                 "prefix"         :    pipeline.prefix+'_HI',
#                 "field"          :    target,
                 "mode"           :    'channel',
                 "nchan"          :    nchans,
                 "start"          :    config['casa_image'].get('startchan', 0,),
                 "interpolation"  :    'nearest',
                 "niter"          :    config['casa_image'].get('niter', 1000000),
                 "psfmode"        :    'hogbom',
                 "threshold"      :    config['casa_image'].get('threshold', '10mJy'),
                 "npix"           :    config['casa_image'].get('npix', npix),
                 "cellsize"       :    config['casa_image'].get('cell', cell),
                 "weight"         :    config['casa_image'].get('weight', weight),
                 "robust"         :    config['casa_image'].get('robust', robust),
                 "stokes"         :    config['casa_image'].get('pol','I'),
#                 "wprojplanes"    :    1,
                 "port2fits"      :    True,
                 "restfreq"       :    restfreq,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='{:s}:: Image HI'.format(step))

    if pipeline.enable_task(config,'freq_to_vel'):
        for ss in ['dirty','psf','residual','model','image']:
            cubename=pipeline.prefix+'_HI.'+ss+'.fits:'+pipeline.output
            recipe.add(freq_to_vel, 'spectral_header_to_vel_radio_{0:s}_cube'.format(ss),
                       {
                           'filename' : cubename,
                       },
                       input=pipeline.input,
                       output=pipeline.output,
                       label='Convert spectral axis from frequency to radio velocity for cube {0:s}'.format(cubename))

    if pipeline.enable_task(config, 'sofia'):
        step = 'sofia_sources'
        recipe.add('cab/sofia', step,
            {
            "import.inFile"         : pipeline.prefix+'_HI.image.fits:output',
            "steps.doFlag"          : config['sofia'].get('flag', False),
            "steps.doScaleNoise"    : True,
            "steps.doSCfind"        : True,
            "steps.doMerge"         : config['sofia'].get('merge', True),
            "steps.doReliability"   : False,
            "steps.doParameterise"  : False,
            "steps.doWriteMask"     : True,
            "steps.doMom0"          : True,
            "steps.doMom1"          : False,
            "steps.doWriteCat"      : False,
            "flag.regions"          : config['sofia'].get('flagregion', []),
            "scaleNoise.statistic"  : config['sofia'].get('rmsMode', 'mad'),
            "SCfind.threshold"      : config['sofia'].get('threshold', 4),
            "SCfind.rmsMode"        : config['sofia'].get('rmsMode', 'mad'),
            "merge.radiusX"         : config['sofia'].get('mergeX', 2),
            "merge.radiusY"         : config['sofia'].get('mergeY', 2),
            "merge.radiusZ"         : config['sofia'].get('mergeZ', 3),
            "merge.minSizeX"        : config['sofia'].get('minSizeX', 3),
            "merge.minSizeY"        : config['sofia'].get('minSizeY', 3),
            "merge.minSizeZ"        : config['sofia'].get('minSizeZ', 5),
            },
            input=pipeline.input,
            output=pipeline.output,
            label='{0:s}:: Make SoFiA mask and images'.format(step))

    if pipeline.enable_task(config, 'flagging_summary'):
        for i,msname in enumerate(mslist):
            step = 'flagging_summary_image_HI_{0:d}'.format(i)
            recipe.add('cab/casa_flagdata', step,
                {
                  "vis"         : msname,
                  "mode"        : 'summary',
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Flagging summary  ms={1:s}'.format(step, msname))

           
