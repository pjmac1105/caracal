import os
import sys
import meerkathi
import stimela.dismissable as sdm

NAME = 'Split and average target data'
# Rules for interpolation mode to use when applying calibration solutions
applycal_interp_rules = {
   'target'   :  {
                  'delay_cal'      : 'linear',
                  'bp_cal'         : 'linear',
                  'transfer_fluxscale': 'linear',
                  'gain_cal_gain'  : 'linear',
                 },
}

get_dir_path = lambda string,pipeline : string.split(pipeline.output)[1][1:]

table_suffix = {
    "delay_cal"             : 'K0',
    "bp_cal"                : 'B0',
    "gain_cal_gain"         : 'G0',
    "gain_cal_flux"         : 'G0',
    "transfer_fluxscale"    : 'F0',
}

# Check if field was specified as known key, else return the
# same value.

def worker(pipeline, recipe, config):

    def get_field(field):
            """
                gets field ids parsed previously in the pipeline
                params:
                    field: list of ids or comma-seperated list of ids where
                           ids are in bpcal, gcal, target, fcal or an actual field name
            """
            return ','.join(filter(lambda s: s != "", map(lambda x: ','.join(getattr(pipeline, x)[i].split(',')
                                                if isinstance(getattr(pipeline, x)[i], str) and getattr(pipeline, x)[i] != "" else getattr(pipeline, x)[i])
                                              if x in ['bpcal', 'gcal', 'target', 'fcal', 'xcal']
                                              else x.split(','),
                                field.split(',') if isinstance(field, str) else field)))

    def get_gain_field(applyme, applyto=None):
            if applyme == 'delay_cal':
                return get_field(config['apply_delay_cal'].get('field', ['bpcal','gcal','xcal']))
            if applyme == 'bp_cal':
                return get_field(config['apply_bp_cal'].get('field', ['bpcal']))
            if applyme == 'gain_cal_flux':
                return get_field('fcal')
            if applyme == 'gain_cal_gain':
                return get_field('gcal')
            if applyme == 'transfer_fluxscale':
                return get_field('gcal')


    label = config['label_out']
    label_in = config['label_in']
    pipeline.set_cal_msnames(label)
    pipeline.set_hires_msnames(label_in)

#    hires_label = config['hires_split'].get('hires_label', 'hires')
#    print hires_label
#    pipeline.set_hires_msnames(label_out)
#    if pipeline.enable_task(config, 'otfcal'):
#       print "OTF calibartion enabled"
#       pipeline.set_hires_msnames(hires_label)


    for i in range(pipeline.nobs):
#        msname = pipeline.msnames[i]
        fms = pipeline.hires_msnames[i]
        target = pipeline.target[0]
        prefix = pipeline.prefixes[i]
        tms = pipeline.cal_msnames[i]
        flagv = tms + '.flagversions'

        if pipeline.enable_task(config['split_target']	, 'otfcal'):                #write calibration library file for OTF cal in split_target_worker.py
            
	    import getpass
	    uname = getpass.getuser()
	    gaintablelist,gainfieldlist,interplist = [],[],[]
            callabel = config['split_target']['otfcal'].get('callabel', '')
            calprefix = '{0:s}-{1:s}'.format(prefix, callabel)            

	    for applyme in 'delay_cal bp_cal gain_cal_flux gain_cal_gain transfer_fluxscale'.split():
		if not pipeline.enable_task(config, 'apply_'+applyme):
                   continue
                suffix = table_suffix[applyme]
                interp = applycal_interp_rules['target'][applyme]
                gainfield = get_gain_field(applyme, 'target')
                gaintablelist.append('{0:s}/{1:s}.{2:s}'.format(get_dir_path(pipeline.caltables, pipeline), calprefix, suffix))
                gainfieldlist.append(gainfield)
                interplist.append(interp)

            with open(os.path.join(pipeline.output, 'callib_target_'+callabel+'.txt'), 'w') as stdw:
		for j in range(len(gaintablelist)):
       			stdw.write('caltable=\'/home/'+uname+'/'+os.path.join(pipeline.output, gaintablelist[j])+'\'')
			stdw.write(' calwt=False')
			stdw.write(' tinterp=\''+str(interplist[j])+'\'')
			stdw.write(' finterp=\'linear\'')
			stdw.write(' fldmap=\'' +str(gainfieldlist[j])+'\'\n')

        if pipeline.enable_task(config, 'split_target'):
            step = 'split_target_{:d}'.format(i)
            if os.path.exists('{0:s}/{1:s}'.format(pipeline.msdir, tms)) or \
                   os.path.exists('{0:s}/{1:s}'.format(pipeline.msdir, flagv)):

                os.system('rm -rf {0:s}/{1:s} {0:s}/{2:s}'.format(pipeline.msdir, tms, flagv))

            recipe.add('cab/casa_mstransform', step,
                {
                    "vis"           : fms,
                    "outputvis"     : tms,
                    "timebin"       : config['split_target'].get('time_average', ''),
                    "width"         : config['split_target'].get('freq_average', 1),
                    "spw"           : config['split_target'].get('spw', ''),
                    "datacolumn"    : config['split_target'].get('column', 'data'),
                    "correlation"   : config['split_target'].get('correlation', ''),
                    "field"         : str(target),
                    "keepflags"     : True,
                    "docallib"      : config['split_target']['otfcal'].get('enable', False),
                    "callib"        : 'callib_target_'+callabel+'.txt',
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Split and average data ms={1:s}'.format(step, fms))


 
        if pipeline.enable_task(config, 'prepms'):
            step = 'prepms_{:d}'.format(i)
            recipe.add('cab/msutils', step,
                {
                  "msname"  : tms,
                  "command" : 'prep' ,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Add BITFLAG column ms={1:s}'.format(step, tms))

        if pipeline.enable_task(config, 'changecentre'):
            if config['changecentre'].get('ra','') == '' or config['changecentre'].get('dec','') == '':
                meerkathi.log.error('Wrong format for RA and/or Dec you want to change to. Check your settings of split_target:changecentre:ra and split_target:changecentre:dec')
                meerkathi.log.error('Current settings for ra,dec are {0:s},{1:s}'.format(config['changecentre'].get('ra',''),config['changecentre'].get('dec','')))
                sys.exit(1)
            step = 'changecentre_{:d}'.format(i)
            recipe.add('cab/casa_fixvis', step,
                {
                  "msname"  : tms,
                  "outputvis": tms,
                  "phasecenter" : 'J2000 {0:s} {1:s}'.format(config['changecentre'].get('ra',''),config['changecentre'].get('dec','')) ,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Change phase centre ms={1:s}'.format(step, tms))


        if (pipeline.enable_task(config, 'changecentre') and pipeline.enable_task(config, 'hires_split')):
            if config['changecentre'].get('ra','') == '' or config['changecentre'].get('dec','') == '':
                meerkathi.log.error('Wrong format for RA and/or Dec you want to change to. Check your settings of split_target:changecentre:ra and split_target:changecentre:dec')
                meerkathi.log.error('Current settings for ra,dec are {0:s},{1:s}'.format(config['changecentre'].get('ra',''),config['changecentre'].get('dec','')))
                sys.exit(1)
            step = 'changecentre_{:d}_hires'.format(i)
            recipe.add('cab/casa_fixvis', step,
                {
                  "msname"  : fms,
                  "outputvis": fms,
                  "phasecenter" : 'J2000 {0:s} {1:s}'.format(config['changecentre'].get('ra',''),config['changecentre'].get('dec','')) ,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Change phase centre ms={1:s}'.format(step, fms))


        if pipeline.enable_task(config, 'obsinfo'):
            if (config['obsinfo'].get('listobs', True) and pipeline.enable_task(config, 'split_target')):
                step = 'listobs_{:d}'.format(i)
                recipe.add('cab/casa_listobs', step,
                    {
                      "vis"         : tms,
                      "listfile"    : '{0:s}-{1:s}-obsinfo.txt'.format(prefix, label),
                      "overwrite"   : True,
                    },
                    input=pipeline.input,
                    output=pipeline.output,
                    label='{0:s}:: Get observation information ms={1:s}'.format(step, tms))
    
            if (config['obsinfo'].get('summary_json', True) and pipeline.enable_task(config, 'split_target')):
                 step = 'summary_json_{:d}'.format(i)
                 recipe.add('cab/msutils', step,
                    {
                      "msname"      : tms,
                      "command"     : 'summary',
                      "outfile"     : '{0:s}-{1:s}-obsinfo.json'.format(prefix, label),
                    },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Get observation information as a json file ms={1:s}'.format(step, tms))

        if (pipeline.enable_task(config, 'obsinfo') and pipeline.enable_task(config, 'hires_split')):
            if config['obsinfo'].get('listobs', True):
                step = 'listobs_{:d}_hires'.format(i)
                recipe.add('cab/casa_listobs', step,
                    {
                      "vis"         : fms,
                      "listfile"    : '{0:s}-{1:s}-obsinfo.txt'.format(prefix, hires_label),
                      "overwrite"   : True,
                    },
                    input=pipeline.input,
                    output=pipeline.output,
                    label='{0:s}:: Get observation information ms={1:s}'.format(step, tms))

            if (config['obsinfo'].get('summary_json', True) and pipeline.enable_task(config, 'hires_split')):
                 step = 'summary_json_{:d}_hires'.format(i)
                 recipe.add('cab/msutils', step,
                    {
                      "msname"      : fms,
                      "command"     : 'summary',
                      "outfile"     : '{0:s}-{1:s}-obsinfo.json'.format(prefix, hires_label),
                    },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Get observation information as a json file ms={1:s}'.format(step, fms))
