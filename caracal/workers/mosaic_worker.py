# -*- coding: future_fstrings -*-
import os
import glob
import sys
import caracal
import numpy as np
from astropy import units as u
import astropy.coordinates as coord
from astropy.io import fits
from astropy import wcs
from caracal.dispatch_crew import utils

NAME = "Mosaic Images and Cubes"
LABEL = 'mosaic'


def worker(pipeline, recipe, config):

    wname = pipeline.CURRENT_WORKER

    ##########################################
    # Defining functions for the worker
    ##########################################

    # Not using anymore, but might need later
    def identify_last_selfcal_image(directory_to_check, prefix, field, mfsprefix):

        # Doing this because convergence may have been reached before the user-specified number of iterations
        matching_files = glob.glob(directory_to_check+'/{0:s}_{1:s}_*{2:s}-image.fits'.format(
            prefix, field, mfsprefix))  # '*' to pick up the number
        max_num = 0  # Initialisation

        for filename in matching_files:
            split_filename = filename.split('_')
            number = split_filename[-1].split('-')[0]
            num = int(number)
            if num > max_num:
                max_num = num

        filename_of_last_selfcal_image = '{0:s}_{1:s}_{2:s}{3:s}-image.fits'.format(
            prefix, field, str(max_num),  mfsprefix)
        return filename_of_last_selfcal_image

    def identify_last_subdirectory(directory_to_check, mosaictype):

        max_num = 0  # Initialisation

        # Subdirectory prefix depends on whether we are looking in pipeline.continuum or pipeline.cubes
        if mosaictype == 'continuum':
            subdirectory_prefix = 'image_'
        else:  # i.e.  mosaictype == 'spectral'
            subdirectory_prefix = 'cube_'

        matching_subdirectories = glob.glob(
            directory_to_check+'/'+subdirectory_prefix+'*')  # '*' to pick up the number

        for subdirectory in matching_subdirectories:
            split_subdirectory = subdirectory.split('_')
            # In case there is one or more '_' in the directory name, want to get the last portion
            number = split_subdirectory[-1]
            num = int(number)
            if num > max_num:
                max_num = num

        last_subdirectory = subdirectory_prefix + str(max_num)
        return max_num, last_subdirectory

    # Copied from masking_worker.py and edited. This is to get a Gaussian beam.
    def build_beam(obs_freq, centre, cell, imsize, out_beam):

        # if copy_head == True:
        #    hdrfile = fits.open(headfile)
        #    hdr = hdrfile[0].header
        # elif copy_head == False:

        w = wcs.WCS(naxis=2)

        # Using u.deg for both due to using 'CRVAL1' and 'CRVAL2' to set the centre
        centre = coord.SkyCoord(
            centre[0], centre[1], unit=(u.deg, u.deg), frame='icrs')
        # cell /= 3600.0  # Am assuming that cell was passed to the function in units of arcsec, so this is converting it to units of deg.
        # Commenting the above out as 'CDELT2' from the corresponding image will be passed to the function, and this is already in deg.

        # The '+ 1's are needed to avoid a shape mismatch later on
        w.wcs.crpix = [(imsize/2) + 1, (imsize/2) + 1]
        w.wcs.cdelt = np.array([-cell, cell])
        w.wcs.crval = [centre.ra.deg, centre.dec.deg]
        w.wcs.ctype = ["RA---SIN", "DEC--SIN"]

        hdr = w.to_header()
        hdr['SIMPLE'] = 'T'
        hdr['BITPIX'] = -32
        hdr['NAXIS'] = 2
        hdr.set('NAXIS1',  imsize, after='NAXIS')
        hdr.set('NAXIS2',  imsize, after='NAXIS1')

        if 'CUNIT1' in hdr:
            del hdr['CUNIT1']
        if 'CUNIT2' in hdr:
            del hdr['CUNIT2']

        # Units of m. The default assumes that MeerKAT data is being processed
        dish_diameter = config['dish_diameter']
        pb_fwhm_radians = 1.02*(2.99792458E8/obs_freq)/dish_diameter
        pb_fwhm = 180.0*pb_fwhm_radians/np.pi   # Now in units of deg
        pb_fwhm_pix = pb_fwhm/hdr['CDELT2']
        x, y = np.meshgrid(np.linspace(-hdr['NAXIS2']/2.0, hdr['NAXIS2']/2.0, hdr['NAXIS2']),
                           np.linspace(-hdr['NAXIS1']/2.0, hdr['NAXIS1']/2.0, hdr['NAXIS1']))
        d = np.sqrt(x*x+y*y)
        sigma, mu = pb_fwhm_pix/2.35482, 0.0  # sigma = FWHM/sqrt(8ln2)
        gaussian = np.exp(-((d-mu)**2 / (2.0 * sigma**2)))

        fits.writeto(out_beam, gaussian, hdr, overwrite=True)

    # Copied from line_worker.py and edited. This is to get a Mauchian beam.
    # The original version makes the build_beam function above redundant but do not want to change too many things at once.
    def make_mauchian_pb(filename, apply_corr, typ, dish_size):
        if not os.path.exists(filename):
            caracal.log.warn(
                'Skipping primary beam cube for {0:s}. File does not exist.'.format(filename))
        else:
            with fits.open(filename) as cube:
                headcube = cube[0].header
                datacube = np.indices(
                    (headcube['naxis2'], headcube['naxis1']), dtype=np.float32)
                datacube[0] -= (headcube['crpix2'] - 1)
                datacube[1] -= (headcube['crpix1'] - 1)
                datacube = np.sqrt((datacube**2).sum(axis=0))
                datacube.resize((1, datacube.shape[0], datacube.shape[1]))
                datacube = np.repeat(datacube,
                                 headcube['naxis3'],
                                 axis=0) * np.abs(headcube['cdelt1'])
                freq = (headcube['crval3'] + headcube['cdelt3'] * (
                    np.arange(headcube['naxis3']) - headcube['crpix3'] + 1))
                if typ == 'gauss':
                    sigma_pb = 17.52 / (freq / 1e+9) / dish_size / 2.355
                    sigma_pb.resize((sigma_pb.shape[0], 1, 1))
                    datacube = np.exp(-datacube**2 / 2 / sigma_pb**2)
                elif typ == 'mauch':
                    FWHM_pb = (57.5/60) * (freq / 1.5e9)**-1
                    FWHM_pb.resize((FWHM_pb.shape[0], 1, 1))
                    datacube = (np.cos(1.189 * np.pi * (datacube / FWHM_pb)) / (
                           1 - 4 * (1.189 * datacube / FWHM_pb)**2))**2
                fits.writeto(filename.replace('image.fits','pb.fits'),
                    datacube, header=headcube, overwrite=True)
                if apply_corr:
                    fits.writeto(filename.replace('image.fits','pb_corr.fits'),
                        cube[0].data / datacube, header=headcube, overwrite=True)  # Applying the primary beam correction
                caracal.log.info('Created primary beam cube FITS {0:s}'.format(
                    filename.replace('image.fits', 'pb.fits')))



    ##########################################
    # Main part of the worker
    ##########################################

    # Prioritise parameters specified in the config file, under the 'mosaic' worker
    # i.e. 'continuum' or 'spectral'
    specified_mosaictype = config['mosaic_type']
    use_mfs_images = config['use_mfs']
    specified_images = config['target_images']
    label = config['label_in']
    line_name = config['line_name']
    pb_type = config['pb_type']

    # Parameters that depend on the mosaictype
    if specified_mosaictype == 'spectral':
        pb_origin = 'generated by the image_line_worker'
        input_directory = pipeline.cubes
    else:
        pb_origin = 'that are already in place (generated by the self_cal_worker, or during a previous run of the mosaic_worker)'
        input_directory = pipeline.continuum
        if specified_mosaictype == 'continuum':
            pass
        else:
            caracal.log.info(
                "Mosaic_type was not set to either 'continuum' or 'spectral' in the config file, so proceeding with default mode ('continuum').")
            specified_mosaictype = 'continuum'

    # To ease finding the appropriate files, and to keep this worker self-contained
    if use_mfs_images == True:
        mfsprefix = '-MFS'
    else:
        mfsprefix = ''

    ## please forget pipeline.dataid: it is now pipeline.msbasenames
    #pipeline.prefixes = ['{0:s}-{1:s}-{2:s}'.format(pipeline.prefix,did,config['label_in']) for did in pipeline.dataid]
    # In case there are different pipeline prefixes
    # for i in range(len(pipeline.prefixes)): ### I may need to put this loop back in later

    prefix = pipeline.prefix

    # If nothing is passed via the config file, then specified_images[0] adopts this via the schema
    if specified_images[0] == 'directory/first_image.fits':

        caracal.log.info(
            "No image names were specified via the config file, so they are going to be selected automatically.")
        caracal.log.info(
            "It is assumed that they are all in the highest-numbered subdirectory of 'output/continuum' and 'output/cubes'.")
        caracal.log.info(
            "You should check the selected image names. If unhappy with the selection, please use a config file to specify the correct ones to use.")

        # Needed for working out the field names for the targets, so that the correct files can be selected
        all_targets, all_msfile, ms_dict = pipeline.get_target_mss(label)
        n_targets = len(all_targets)
        caracal.log.info(
            'The number of targets to be mosaicked is {0:d}'.format(n_targets))

        # Where the targets are in the output directory
        max_num, last_subdirectory = identify_last_subdirectory(
            input_directory, specified_mosaictype)

        # Empty list to add filenames to
        pathnames = []
        specified_images = []

        # Expecting the same prefix and mfsprefix to apply for all fields to be mosaicked together
        for target in all_targets:

            field = utils.filter_name(target)

            # Use the mosaictype to infer the filenames of the images
            if specified_mosaictype == 'continuum':  # Add name of 2D image output by selfcal

                path_to_image = pipeline.continuum
                pathnames.append(path_to_image)

                image_name = '{0:s}/{1:s}_{2:s}_{3:s}{4:s}-image.fits'.format(
                    last_subdirectory, prefix, field, str(max_num),  mfsprefix)
                # Note that the path is not included in image_name
                specified_images.append(image_name)

            else:  # i.e. mosaictype = 'spectral', so add name of cube output by image_line

                path_to_cube = pipeline.cubes
                pathnames.append(path_to_cube)

                image_name = '{0:s}/{1:s}_{2:s}_{3:s}{4:s}-image.fits'.format(
                    last_subdirectory, prefix, field, line_name, mfsprefix)  # max_num not needed for finalcubename
                if mfsprefix == '':
                    # Following the naming in image_line_worker
                    image_name = image_name.replace('-image', '.image')
                # Note that the path is not included in image_name
                specified_images.append(image_name)

    else:  # i.e. if the user has specified images, they have hopefully included which subdirectory of 'continuum' or 'cubes' each image is in

        pathnames = [input_directory] * len(specified_images)

    caracal.log.info('PLEASE CHECK -- Images to be mosaicked are:')
    caracal.log.info(specified_images)
    caracal.log.info('Corresponding pathnames are:')
    caracal.log.info(pathnames)

    # Although montage_mosaic checks whether pb.fits files are present, we need to do this earlier in the worker,
    # so that we can create simple Gaussian (or Mauchian) primary beams if need be
    for image_name in specified_images:

        pb_name = image_name.replace('image.fits', 'pb.fits')

        # Need the corresponding pathname for the image being considered
        index_to_use = specified_images.index(image_name)
        pathname = pathnames[index_to_use]

        if os.path.exists(pathname + '/' + pb_name):
            caracal.log.info(
                '{0:s}/{1:s} is already in place, and will be used by montage_mosaic.'.format(pathname, pb_name))

        else:

            if specified_mosaictype == 'spectral':
                caracal.log.error(
                    '{0:s}/{1:s} does not exist. Please make sure that it is in place before proceeding.'.format(pathname, pb_name))
                caracal.log.error(
                    'You may need to re-run the image_line worker with pb_cube enabled. EXITING.')
                raise caracal.ConfigurationError("missing primary beam file {}/{}".format(pathname, pb_name))

            else:  # i.e. mosaictype == 'continuum'

                caracal.log.info(
                    '{0:s}/{1:s} does not exist, so going to create a pb.fits file instead.'.format(pathname, pb_name))

                if pb_type == 'gaussian':
                
                    # Create rudimentary primary-beam, which is assumed to be a Gaussian with FWMH = 1.02*lambda/D
                    image_hdu = fits.open(pathname + '/' + image_name)
                    image_header = image_hdu[0].header
                    # i.e. [ RA, Dec ]. Assuming that these are in units of deg.
                    image_centre = [image_header['CRVAL1'], image_header['CRVAL2']]
                    # Again assuming that these are in units of deg.
                    image_cell = image_header['CDELT2']
                    image_imsize = image_header['NAXIS1']

                    recipe.add(build_beam, 'build_gaussian_pb',
                               {
                                   # Units of Hz. The default assumes that MeerKAT data is being processed
                                   'obs_freq': config['ref_frequency'],
                                   'centre': image_centre,
                                   'cell': image_cell,
                                   'imsize': image_imsize,
                                   'out_beam': pathname + '/' + pb_name,
                               },
                               input=pipeline.input,
                               # Was pipeline=pipeline.output before the restructure of the output directory
                               output=pathname,
                               label='build_gaussian_pb:: Generating {0:s}'.format(pathname + '/' + pb_name))

                    # Confirming freq and dish_diameter values being used for the primary beam
                    caracal.log.info('Observing frequency = {0:f} Hz, dish diameter = {1:f} m'.format(
                        config['ref_frequency'], config['dish_diameter']))


                else:  # i.e. pb_type == 'mauchian'

                    ### CHECK WHAT DANE DID


                pb_origin = 'generated by the mosaic_worker'


    caracal.log.info('Checking for *pb.fits files now complete.')

    # Will need it later, unless Sphe has a more elegant method
    original_working_directory = os.getcwd()

    caracal.log.info(
        'Now creating symlinks to images and beams, in case they are distributed across multiple subdirectories')
    # To get the symlinks created in the correct directory
    os.chdir(input_directory)

    # Empty list to add filenames to, as we are not to pass 'image_1', etc, to the recipe
    image_filenames = []

    # Start by assuming that 'image' is of the form 'image_1/image_filename'
    for specified_image in specified_images:

        split_imagename = specified_image.split('/')
        subdirectory = split_imagename[0]
        image_filename = split_imagename[1]
        image_filenames.append(image_filename)

        symlink_for_image_command = 'ln -sf ' + specified_image + ' ' + image_filename
        os.system(symlink_for_image_command)

        # Also need a symlink for the corresponding pb file
        specified_beam = specified_image.replace('image.fits', 'pb.fits')
        beam_filename = image_filename.replace('image.fits', 'pb.fits')

        symlink_for_beam_command = 'ln -sf ' + specified_beam + ' ' + beam_filename
        os.system(symlink_for_beam_command)

    # To get back to where we were before symlink creation
    os.chdir(original_working_directory)

    # Prefix of the output files should be either the default (pipeline.prefix) or that specified by the user via the config file
    mosaic_prefix = config['name']
    if mosaic_prefix == '':  # i.e. this has been set via the schema
        mosaic_prefix = pipeline.prefix

    # List of images in place, and have ensured that there are corresponding pb.fits files,
    # so now ready to add montage_mosaic to the caracal recipe
    if pipeline.enable_task(config, 'domontage'):
        recipe.add('cab/mosaicsteward', 'mosaic-steward',
                   {
                       "mosaic-type": specified_mosaictype,
                       "domontage": True,
                       "cutoff": config['cutoff'],
                       "name": mosaic_prefix,
                       "target-images": image_filenames,
                   },
                   input=input_directory,
                   output=pipeline.mosaics,
                   label='MosaicSteward:: Re-gridding {0:s} images before mosaicking them. For this mode, the mosaic_worker is using *pb.fits files {1:s}.'.format(specified_mosaictype, pb_origin))

    else:  # Written out for clarity as to what difference the 'domontage' setting makes
        recipe.add('cab/mosaicsteward', 'mosaic-steward',
                   {
                       "mosaic-type": specified_mosaictype,
                       "domontage": False,
                       "cutoff": config['cutoff'],
                       "name": mosaic_prefix,
                       "target-images": image_filenames,
                   },
                   input=input_directory,
                   output=pipeline.mosaics,
                   label='MosaicSteward:: Re-gridding of images and beams is assumed to be already done, so straight to mosaicking {0:s} images. For this mode, the mosaic_worker is using *pb.fits files {1:s}.'.format(specified_mosaictype, pb_origin))
