import os, glob
import sys
from astropy import units as u
import astropy.coordinates as coord
from astropy.io import fits
from astropy import wcs

NAME = "Mosaic specified images, or those output by selfcal or imageHI"

def worker(pipeline, recipe, config):

    wname = pipeline.CURRENT_WORKER

    ##########################################
    # Defining functions for the worker
    ##########################################

    def identify_last_selfcal_image(directory_to_check, prefix, field, mfsprefix):
        
        # Doing this because convergence may have been reached before the user-specified number of iterations
        matching_files = glob.glob(directory_to_check+'/{0:s}_{1:s}_*{2:s}-image.fits'.format(prefix, field, mfsprefix) # '*' to pick up the number
        max_num = 0  # Initialisation
        
        for filename in matching_files:
            split_filename = filename.split('_')
            number = split_filename[-1].split('-')[0]
            num = int(number)
            if num > max_num:
                max_num = num
        
        filename_of_last_selfcal_image = '{0:s}_{1:s}_{2:s}{3:s}-image.fits'.format(prefix, field, str(max_num),  mfsprefix)
        return filename_of_last_selfcal_image


    def build_beam(obs_freq,centre,cell,imsize,out_beam):  # Copied from masking_worker.py and edited

        #if copy_head == True:
        #    hdrfile = fits.open(headfile)
	#    hdr = hdrfile[0].header
	#elif copy_head == False:

        w = wcs.WCS(naxis=2)

        centre = coord.SkyCoord(centre[0], centre[1], unit=(u.hourangle, u.deg), frame='icrs')
        cell /= 3600.0

        w.wcs.crpix = [imsize/2, imsize/2]
        w.wcs.cdelt = np.array([-cell, cell])
        w.wcs.crval = [centre.ra.deg, centre.dec.deg]
        w.wcs.ctype = ["RA---SIN", "DEC--SIN"]

        hdr = w.to_header()
        hdr['SIMPLE']  = 'T'
        hdr['BITPIX']  = -32
        hdr['NAXIS']   = 2
        hdr.set('NAXIS1',  imsize, after='NAXIS')
        hdr.set('NAXIS2',  imsize, after='NAXIS1')

        if 'CUNIT1' in hdr:
            del hdr['CUNIT1']
        if 'CUNIT2' in hdr:
            del hdr['CUNIT2']

        dish_diameter = config.get('dish_diameter', 13.5) # Units of m. The default assumes that MeerKAT data is being processed
        pb_fwhm_radians = 1.02*(2.99792458E8/obs_freq)/dish_diameter
        pb_fwhm = 180.0*pb_fwhm_radians/np.pi   # Now in units of deg
        pb_fwhm_pix = pb_fwhm/hdr['CDELT2']
        x, y = np.meshgrid(np.linspace(-hdr['NAXIS2']/2.0,hdr['NAXIS2']/2.0,hdr['NAXIS2']),
				    np.linspace(-hdr['NAXIS1']/2.0,hdr['NAXIS1']/2.0,hdr['NAXIS1']))
        d = np.sqrt(x*x+y*y)
        sigma, mu = pb_fwhm_pix/2.35482, 0.0  # sigma = FWHM/sqrt(8ln2)
        gaussian = np.exp(-( (d-mu)**2 / ( 2.0 * sigma**2 ) ) )

        fits.writeto(out_beam,gaussian,hdr,overwrite=True)


    ##########################################
    # Main part of the worker
    ##########################################

    # Prioritise parameters specified in the config file, under the 'mosaic' worker   ### How do we get them from the schema instead?
    specified_mosaictype = config['mosaic_type'] # i.e. 'continuum' or 'spectral'
    specified_cutoff = config['cutoff'] # e.g. 0.25  ### Not immediately needed, so could be removed here?
    use_MFS_images = config['use_MFS_images']
    specified_prefix = config['name']
    specified_images = config['target_images'] ### Test at a later point

    # Parameters obtained from the config file, but under another worker
    if specified_mosaictype = 'continuum':
        label = pipeline.config['self_cal']['label'] ### Adopt the default for this
    if specified_mosaictype = 'spectral':
        label = pipeline.config['image_HI']['label'] ### Adopt the default for this
    else:
        label = pipeline.config['self_cal']['label'] ### Adopt the default for this
        meerkathi.log.info("Mosaic_type was not set to either 'continuum' or 'spectral' in the config file, so proceeding with default mode ('continuum').")
        specified_mosaictype = 'continuum'

    # To ease finding the appropriate files, and to keep this worker self-contained
    if use_MFS_images = 'true':
        mfsprefix = '-MFS'
    else:
        mfsprefix = ''

    # In case there are different pipeline prefixes
    for i in range(pipeline.nobs):

        prefix = pipeline.prefixes[i]

        if specified_images is None: ### Check that this is the correct way to check that nothing is passed via the config file

            # Needed for working out the field names for the targets 
            all_targets, all_msfile, ms_dict = utils.target_to_msfiles(pipeline.target[i],pipeline.msnames[i],label) ### Just following the pattern in adding '[i]'...

            # Empty list to add filenames to
            specified_images = []

            # Expecting the same prefix and mfsprefix to apply for all fields to be mosaicked together
            for target in all_targets:

                field = utils.filter_name(target)

                # Use the mosaictype to infer the filenames of the images
                if specified_mosaictype = 'continuum':  # Add name of 2D image output by selfcal
                    image_name = identify_last_selfcal_image(pipeline.output, prefix, field, mfsprefix)
                    specified_images = specified_images.append(image_name)
                    pb_origin = 'generated by the mosaic_worker'
                else:  # i.e. mosaictype = 'spectral', so add name of cube output by imageHI
                    image_name = '{0:s}_{1:s}_HI{2:s}-image.fits'.format(prefix, field, mfsprefix)
                    if mfsprefix = '':
                        image_name = image_name.replace('-image','.image') # Following the naming in image_HI_worker   
                    specified_images = specified_images.append(image_name)
                    pb_origin = 'generated by the image_HI_worker'

        # Although montage_mosaic checks whether pb.fits files are present, we need to do this earlier in the worker,
        # so that we can create simple Gaussian primary beams if need be
        for image_name in specified_images:
            
            pb_name = image_name.replace('image.fits', 'pb.fits')
            if os.path.exists(pipeline.output+'/'+pb_name):
                meerkathi.log.info('{0:s} is already in place, and will be used by montage_mosaic.'.format(pb_name))
            else:
                meerkathi.log.info('{0:s} does not exist, so going to create a rudimentary pb.fits file instead.'.format(pb_name))

                # Create rudimentary primary-beam, which is assumed to be a Gaussian with FWMH = 1.02*lambda/D
                image_centre =
                image_cell = 
                image_imsize =

                recipe.add(build_beam, 'build gaussian primary beam',
		    {
		        'obs_freq' : config.get('frequency', 1383685546.875), # Units of Hz. The default assumes that MeerKAT data is being processed
		        'centre'   : image_centre,
		        'cell'     : image_cell,
		        'imsize'   : image_imsize,
		        'out_beam' : pb_name,
		    },
		    input=pipeline.input,
                    output=pipeline.output)
                 

        # List of images in place, and have ensured that there are corresponding pb.fits files,
        # so now ready to add montage_mosaic to the meerkathi recipe
        if pipeline.enable_task(config, 'domontage'):
            recipe.add('cab/montage_mosaic', 'montage_mosaic',
                {
                    "mosaic-type"    : specified_mosaictype,
                    "domontage"      : True,
                    "cutoff"         : specified_cutoff,
                    "name"           : prefix,
                    "target-images"  : specified_images,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='montage_mosaic:: Re-gridding {0:s} images before mosaicking them. For this mode, the mosaic_worker is using *pb.fits files {1:s}.'.format(specified_mosaictype, pb_origin))
        else:
            recipe.add('cab/montage_mosaic', 'montage_mosaic',
                {
                    "mosaic-type"    : specified_mosaictype,
                    "domontage"      : False,
                    "cutoff"         : specified_cutoff,
                    "name"           : prefix,
                    "target-images"  : specified_images,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='montage_mosaic:: Re-gridding already done, so straight to mosaicking {0:s} images. For this mode, the mosaic_worker is using *pb.fits files {1:s}.'.format(specified_mosaictype, pb_origin))
     
    ### Leaving the following as a reminder of syntax    
    #if pipeline.enable_task(config, 'add_spectral_weights'):
        #step = 'estimate_weights_{:d}'.format(i)
        #recipe.add('cab/msutils', step,
        #    {
        #      "msname"          : msname,
        #      "command"         : 'estimate_weights',
        #      "stats_data"      : config['add_spectral_weights'].get('stats_data', 'use_package_meerkat_spec'),
        #      "weight_columns"  : config['add_spectral_weights'].get('weight_columns', ['WEIGHT', 'WEIGHT_SPECTRUM']),
        #      "noise_columns"   : config['add_spectral_weights'].get('noise_columns', ['SIGMA', 'SIGMA_SPECTRUM']),
        #      "write_to_ms"     : config['add_spectral_weights'].get('write_to_ms', True),
        #      "plot_stats"      : prefix + '-noise_weights.png',
        #    },
        #    input=pipeline.input,
        #    output=pipeline.output,
        #    label='{0:s}:: Adding Spectral weights using MeerKAT noise specs ms={1:s}'.format(step, msname))


