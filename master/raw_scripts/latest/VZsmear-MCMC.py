import matplotlib 
matplotlib.use('agg')
import time
init = time.time()
import numpy as np
from numpy import log, pi,sqrt,cos,sin,argpartition,copy,float32,int32,append,mean,cov,vstack
from astropy.table import Table
from astropy.io import fits
from Corrfunc.theory.DDsmu import DDsmu
import os
import warnings
import matplotlib.pyplot as plt
from multiprocessing import Pool 
from itertools import repeat
import glob
import matplotlib.gridspec as gridspec
from getdist import plots, MCSamples, loadMCSamples
import getdist
import sys
import pymultinest
import h5py

# variables
gal      = sys.argv[1]
GC       = sys.argv[2]
cut      = sys.argv[3]
mode     = sys.argv[4]
date     = '1027'#'1011'#'0919'#'0905'#'0810' 
npoints  = 100 
nseed    = 20
rscale   = 'linear' # 'log'
multipole= 'quad' # 'mono','quad','hexa'
var      = 'Vpeak'  #'Vmax' 'Vpeak'
function = 'mps' # 'wp'
Om       = 0.31
boxsize  = 1000
rmin     = 5
rmax     = 25
nthread  = 64
autocorr = 1
mu_max   = 1
nmu      = 120
autocorr = 1
home      = '/global/cscratch1/sd/jiaxi/master/'
fileroot = 'MCMCout/{}_{}/multinest_'.format(cut,date)


if gal == 'LRG':
    SHAMnum   = int(6.26e4)
    zmin     = 0.6
    zmax     = 1.0
    z = 0.7018
    ver='v7_2'
    halofile = home+'catalog/UNIT_hlist_0.58760.hdf5' 
    #halofile = home+'catalog/UNIT4LRG-cut.hdf5'

if gal == 'ELG':
    SHAMnum   = int(2.93e5)
    zmin     = 0.6
    zmax     = 1.1
    z = 0.8594
    ver='v7'
    halofile = home+'catalog/UNIT_hlist_0.53780.hdf5'

# cosmological parameters
Ode = 1-Om
H = 100*np.sqrt(Om*(1+z)**3+Ode)

# generate linear bins or read log bins
if rscale=='linear':
    # generate s bins
    bins  = np.arange(rmin,rmax+1,1)
    nbins = len(bins)-1
    binmin = rmin
    binmax = rmax
    # generate mu bins   
    s = (bins[:-1]+bins[1:])/2
    mubins = np.linspace(0,mu_max,nmu+1)
    mu = (mubins[:-1]+mubins[1:]).reshape(1,nmu)/2+np.zeros((nbins,nmu))
    # Analytical RR calculation
    RR_counts = 4*np.pi/3*(bins[1:]**3-bins[:-1]**3)/(boxsize**3)	
    rr=((RR_counts.reshape(nbins,1)+np.zeros((1,nmu)))/nmu)
    print('the analytical random pair counts are ready.')

    # covariance matrices and observations
    obs2pcf = '{}catalog/nersc_mps_{}_{}/{}_{}_{}_{}.dat'.format(home,gal,ver,function,rscale,gal,GC)
    covfits  = '{}catalog/nersc_mps_{}_{}/{}_{}_{}_mocks_{}.fits.gz'.format(home,gal,ver,function,rscale,gal,multipole)
    # Read the covariance matrices and observations
    hdu = fits.open(covfits) # cov([mono,quadru])
    mock = hdu[1].data[GC+'mocks']
    Nmock = mock.shape[1] 
    errbar = np.std(hdu[1].data[GC+'mocks'],axis=1)
    hdu.close()
    covR = np.loadtxt('{}catalog/nersc_mps_{}_{}/covR-{}_{}-5_25-{}.dat'.format(home,gal,ver,gal,GC,multipole)
)
    obscf = Table.read(obs2pcf,format='ascii.no_header')[binmin:binmax]  # obs 2pcf
    print('the covariance matrix and the observation 2pcf vector are ready.')
else:
    if gal=='ELG':
        binfile = Table.read(home+'cheng_HOD_{}/mps_log_{}_NGC+SGC_eBOSS_v7_zs_0.70-0.90.dat'.format(gal,gal),format='ascii.no_header')
    else:
        binfile = Table.read(home+'cheng_HOD_{}/mps_log_{}_NGC+SGC_eBOSS_v7_2_zs_0.60-0.80.dat'.format(gal,gal),format='ascii.no_header')   

    bins  = np.unique(np.append(binfile['col1'],binfile['col2']))
    bins = bins[bins<rmax]
    nbins = len(bins)-1

# SHAM halo catalogue, keep them even
read = time.time()
f=h5py.File(halofile,"r")
sel = f["halo"]['Vpeak'][:]>0
if len(f["halo"]['Vpeak'][:][sel])%2 ==1:
    datac = np.zeros((len(f["halo"]['Vpeak'][:][sel])-1,5))
    for i,key in enumerate(f["halo"].keys()):
        datac[:,i] = (f["halo"][key][:][sel])[:-1]
else:
    datac = np.zeros((len(f["halo"]['Vpeak'][:][sel]),5))
    for i,key in enumerate(f["halo"].keys()):
        datac[:,i] = f["halo"][key][:][sel]
f.close()        
half = int32(len(datac)/2)
print(len(datac))
print('read the halo catalogue costs {}s'.format(time.time()-read))

# generate uniform random numbers
print('generating uniform random number arrays...')
uniform_randoms = [np.random.RandomState(seed=1000*x).rand(len(datac)).astype('float32') for x in range(nseed)] 
uniform_randoms1 = [np.random.RandomState(seed=1050*x+1).rand(len(datac)).astype('float32') for x in range(nseed)] 

# HAM application
def sham_tpcf(uni,uni1,sigM,sigV,Mtrun):
    x00,x20,x40=sham_cal(uni,sigM,sigV,Mtrun)
    return [x00,x20]
    #x01,x21,x41=sham_cal(uni1,sigM,sigV,Mtrun)
    #return [(x00+x01)/2,(x20+x21)/2,(x40+x41)/2]

def sham_cal(uniform,sigma_high,sigma,v_high):
    datav = datac[:,1]*(1+append(sigma_high*sqrt(-2*log(uniform[:half]))*cos(2*pi*uniform[half:]),sigma_high*sqrt(-2*log(uniform[:half]))*sin(2*pi*uniform[half:]))) #0.5s
    if cut=='aftercut':
        LRGscat = (datac[datav<v_high])[argpartition(-datav[datav<v_high],SHAMnum)[:(SHAMnum)]]
    elif cut =='precut':
        LRGscat = (datac[datac[:,-1]<v_high])[argpartition(-datav[datac[:,-1]<v_high],SHAMnum)[:(SHAMnum)]]
    elif cut=='indexcut':
        LRGscat = datac[argpartition(-datav,SHAMnum+int(10**v_high))[:(SHAMnum+int(10**v_high))]]
        datav = datav[argpartition(-datav,SHAMnum+int(10**v_high))[:(SHAMnum+int(10**v_high))]]
        LRGscat = LRGscat[argpartition(-datav,int(10**v_high))[int(10**v_high):]]
    else:
        print('wrong input!')
    
    # transfer to the redshift space
    scathalf = int(len(LRGscat)/2)
    z_redshift  = (LRGscat[:,4]+(LRGscat[:,0]+append(sigma*sqrt(-2*log(uniform[:scathalf]))*cos(2*pi*uniform[-scathalf:]),sigma*sqrt(-2*log(uniform[:scathalf]))*sin(2*pi*uniform[-scathalf:])))*(1+z)/H)
    z_redshift %=boxsize
    
    # calculate the 2pcf of the SHAM galaxies
    # count the galaxy pairs and normalise them
    DD_counts = DDsmu(autocorr, nthread,bins,mu_max, nmu,LRGscat[:,2],LRGscat[:,3],z_redshift,periodic=True, verbose=True,boxsize=boxsize)
    # calculate the 2pcf and the multipoles
    mono = (DD_counts['npairs'].reshape(nbins,nmu)/(SHAMnum**2)/rr-1)
    quad = mono * 2.5 * (3 * mu**2 - 1)
    hexa = mono * 1.125 * (35 * mu**4 - 30 * mu**2 + 3)
        # use sum to integrate over mu
    return [np.sum(mono,axis=-1)/nmu,np.sum(quad,axis=-1)/nmu,np.sum(hexa,axis=-1)/nmu]

# chi2
def chi2(sigma_M,sigma_V,M_ceil):
# calculate mean monopole in parallel
    with Pool(processes = nseed) as p:
        xi0_tmp = p.starmap(sham_tpcf,zip(uniform_randoms,uniform_randoms1,repeat(float32(sigma_M)),repeat(float32(sigma_V)),repeat(M_ceil)))
     # average the result for multiple seeds
    xi0,xi2 = mean(xi0_tmp,axis=0,dtype='float32')[0],\
              mean(xi0_tmp,axis=0,dtype='float32')[1]
             #,mean(xi0_tmp,axis=0,dtype='float32')[2]

    # identify the fitting multipoles
    model = append(xi0,xi2)
    mocks = vstack((mock[binmin:binmax,:],mock[binmin+200:binmax+200,:]))
    covcut  = cov(mocks).astype('float32')
    OBS   = append(obscf['col4'],obscf['col5']).astype('float32')  
    # calculate the covariance, residuals and chi2
    res = OBS-model
    return res.dot(covR.dot(res))

if mode == 'debug':
    print('debug mode on, {} used'.format(cut))
    print(chi2(0.5,100.,1.))
else:
    print('multinest active, {} used'.format(cut))
    # prior
    prior_min, prior_max = [],[]
    def prior(cube, ndim, nparams):
        global prior_min,prior_max
        if (gal=='LRG')&(cut =='indexcut'):
            # the same as LRG_SGC_4-n
            cube[0] = 1.2*cube[0]
            cube[1] = 80*cube[1]+70  
            cube[2] = 2.5*cube[2]+4.0 
            prior_min = [0,70,4.0] #[remains, changed, changed]
            prior_max = [1.2,150,6.5] #[changed, remains, changed]
        elif gal=='ELG':
            cube[0] = 1.4*cube[0]+0.4    
            cube[1] = 60*cube[1]      
            cube[2] = 600*cube[2]+200           
            prior_min = [0.4,0,200]
            prior_max = [1.8,60,800]
        else:
            cube[0] = 2.0*cube[0]
            cube[1] = 100*cube[1]+50  
            cube[2] = 2500*cube[2]+500 
            prior_min = [0,50,500]
            prior_max = [2.0,150,3000]

    # loglikelihood = -0.5*chi2    
    def loglike(cube, ndim, nparams):
        sigma_high,sigma,vhigh = cube[0],cube[1],cube[2]
        return -0.5*chi2(sigma_high,sigma,vhigh)   
    #-1*(cube[0]-1)**2/2/2**2 for Gaussian prior

    # number of dimensions our problem has
    parameters = ["sigma","Vsmear","Vceil"]
    npar = len(parameters)

    # run MultiNest & write the parameter's name
    pymultinest.run(loglike, prior, npar,n_live_points= npoints, outputfiles_basename=fileroot, resume =True, verbose = True,n_iter_before_update=5,write_output=True)
    f=open(fileroot+'.paramnames', 'w')
    for param in parameters:
        f.write(param+'\n')
    f.close()

    # prior ranges
    f=open(fileroot+'.ranges', 'w')
    for i,param in enumerate(parameters):
        f.write('{}  {} {}\n'.format(param,prior_min[i],prior_max[i]))
    f.close()

    # getdist plot
    sample = loadMCSamples(fileroot)
    plt.rcParams['text.usetex'] = False
    g = plots.get_single_plotter()
    g.settings.figure_legend_frame = False
    g.settings.alpha_filled_add=0.4
    #g.settings.title_limit_fontsize = 14
    g = plots.get_subplot_plotter()
    g.triangle_plot(sample,parameters, filled=True)#,title_limit=1)
    g.export('{}_{}_{}_posterior.pdf'.format(date,gal,GC))
    plt.close('all')
    # results
    print('Results:')
    stats = sample.getMargeStats()
    best = np.zeros(npar)
    lower = np.zeros(npar)
    upper = np.zeros(npar)
    mean = np.zeros(npar)
    sigma = np.zeros(npar)
    for i in range(npar):
        par = stats.parWithName(parameters[i])
        #best[i] = par.bestfit_sample
        mean[i] = par.mean
        sigma[i] = par.err
        lower[i] = par.limits[0].lower
        upper[i] = par.limits[0].upper
        best[i] = (lower[i] + upper[i]) * 0.5
        print('getdist {0:s}: [{1:.6f}, {2:.6f}]'.format( \
            parameters[i],  lower[i], upper[i]))


    a = pymultinest.Analyzer(npar, outputfiles_basename = fileroot)
    stats = a.get_stats()    
    fin = time.time()  

    file = gal+'_'+GC+'_Vzsmear_report.txt'
    f = open(file,'a')
    f.write('the total {} in {} SHAM costs {:.6} s in 16 cores \n'.format(gal,GC,fin-init))
    f.write('{} {} multinest: \n'.format(gal,GC))
    f.write('(-2)* max loglike: {} \n'.format(-2*a.get_best_fit()['log_likelihood']))
    f.write('max-loglike params: {}\n'.format(a.get_best_fit()['parameters']))
    f.write('\n----------------------------------------------------------------------\n')
    f.write('getdist 1-sigma errors: sigma [{:.6},{:.6}], sigma_smear [{:.6},{:.6}] km/s, Vceil [{:.6},{:.6}] km/s \n'.format(lower[0],upper[0],lower[1],upper[1],lower[2],upper[2]))
    f.write('another way around: sigma {:.6}+{:.6}{:.6}, sigma_smear {:.6}+{:.6}{:.6}km/s,Vceil {:.6}+{:.6}{:.6}km/s  \n'.format(a.get_best_fit()['parameters'][0],upper[0]-a.get_best_fit()['parameters'][0],lower[0]-a.get_best_fit()['parameters'][0],a.get_best_fit()['parameters'][1],upper[1]-a.get_best_fit()['parameters'][1],lower[1]-a.get_best_fit()['parameters'][1],a.get_best_fit()['parameters'][2],upper[2]-a.get_best_fit()['parameters'][2],lower[2]-a.get_best_fit()['parameters'][2]))


    for j in range(npar):
        lower[j], upper[j] = stats['marginals'][j]['1sigma']
        print('getdist {0:s}: [{1:.6f} {2:.6f}]'.format(parameters[j],  upper[j], lower[j]))
    f.write('\n----------------------------------------------------------------------\n')
    f.write('multinest analyser results: sigma [{:.6},{:.6}], sigma_smear [{:.6},{:.6}] km/s, Vceil [{:.6},{:.6}] km/s \n'.format(lower[0],upper[0],lower[1],upper[1],lower[2],upper[2]))
    f.write('another way around: sigma {0:.6}+{1:.6}{2:.6}, sigma_smear {3:.6}+{4:.6}{5:.6}km/s,Vceil {6:.6}+{7:.6}{8:.6}km/s  \n'.format(a.get_best_fit()['parameters'][0],upper[0]-a.get_best_fit()['parameters'][0],lower[0]-a.get_best_fit()['parameters'][0],a.get_best_fit()['parameters'][1],upper[1]-a.get_best_fit()['parameters'][1],lower[1]-a.get_best_fit()['parameters'][1],a.get_best_fit()['parameters'][2],upper[2]-a.get_best_fit()['parameters'][2],lower[2]-a.get_best_fit()['parameters'][2]))
    f.close()
    