#!/usr/bin/python3

# createBetaMap.py
#
# This module reads an ASC file that contains the PfPR for the two to ten age
# bracket and generates three ASC files with beta values.
import argparse
import os
import sys

# Import our libraries
sys.path.append(os.path.join(os.path.dirname(__file__), "include"))

import include.calibrationLib as cl
import include.standards as std
from include.ascFile import load_asc, write_asc
from include.utility import progressBar


# Default path for beta values
BETAVALUES = "data/calibration.csv"

# Starting epsilon and delta to be used
EPSILON = 0.00001
MAX_EPSILON = 0.1

# Warnings to be passed between functions
WARNINGS = ''

def create_beta_map(configuration, gisPath, prefix, age, pfpr_file):
   
    # Load the relevant raster files
    filename = os.path.join(gisPath, std.PFPR_FILE.format(prefix))
    if pfpr_file:
        print('Using supplied PfPR file...')
        filename = pfpr_file    
    [ascHeader, pfpr] = load_asc(filename)
    filename = os.path.join(gisPath, std.POPULATION_FILE.format(prefix))    
    [_, population] = load_asc(filename)

    # Defer to the library to load the rest
    climate = cl.get_climate_zones(configuration, gisPath)
    treatments = cl.get_treatments_raster(configuration, gisPath)
    pfprBand, lookup = cl.load_betas(BETAVALUES)

    # Make sure the data loaded correct, or that there is data
    if len(lookup) == 0:
        raise ValueError("No calibration data is present in the file: {}".format(BETAVALUES))

    # Verify that the age band matches what the file returns, errors are only likely to occur when
    # the user tries to use cached data
    error = None
    if age == '0-59':
        ageBand = '0 to 59 months'
        if pfprBand != 'pfprunder5': error = '0 to 59 months, got 2 to 10 years'
    elif age == '2-10':
        ageBand = '2 to 10 years'
        if pfprBand != 'pfpr2to10': error = '2 to 10 years, got 0 to 59 months'
    if error is not None:
        raise Exception('Mismatch between parameters and calibration file, expected {}'.format(error))

    # Prepare for the ASC data
    epsilons = []
    maxEpsilon = 0
    meanBeta = []

    # Prepare to track the distribution
    distribution = [0] * 5

    # Scan each of the rows 
    print("\nDetermining betas for {}\nRaster Size: {} rows, {} columns".format(ageBand, ascHeader['nrows'], ascHeader['ncols']))
    for row in range(0, ascHeader['nrows']):

        # Append the empty rows
        epsilons.append([])
        meanBeta.append([])

        # Scan each of the PfPR values    
        for col in range(0, ascHeader['ncols']):

            # Append nodata and continue
            if pfpr[row][col] == ascHeader['nodata']:
                epsilons[row].append(ascHeader['nodata'])
                meanBeta[row].append(ascHeader['nodata'])
                continue

            # Get the beta values
            [values, epsilon] = get_betas(climate[row][col], pfpr[row][col], population[row][col], treatments[row][col], lookup)

            # Update the distribution
            for exponent in range(1, len(distribution) + 1):
                if epsilon >= pow(10, -exponent):
                    distribution[exponent - 1] += 1
                    break

            # Was nothing returned?
            if len(values) == 0:
                epsilons[row].append(0)
                meanBeta[row].append(0)
                continue

            # Note the epsilon and the mean
            epsilons[row].append(epsilon)
            if epsilon > maxEpsilon: 
                maxEpsilon = epsilon

                # Determine the population and treatment bin we are working with
                zone = climate[row][col]
                maxValues = "PfPR: {}, Population: {} (Bin: {}), Treatment: {}".format(
                    pfpr[row][col], population[row][col], cl.get_bin(population[row][col], lookup[zone].keys()), treatments[row][col])
            meanBeta[row].append(sum(values) / len(values))

        # Note the progress
        progressBar(row + 1, ascHeader['nrows'])

    # Print the warnings, if any
    if len(WARNINGS) > 0:
        print(WARNINGS)

    # Write the results
    print("\n Max epsilon: {:.6f} / {}".format(maxEpsilon, maxValues))
    print("Epsilon Distribution")
    total = 0
    for ndx in range(0, len(distribution)):
        print("{:>6} : {}".format(pow(10, -(ndx + 1)), distribution[ndx]))
        total += distribution[ndx]
    print("Total Cells: {}".format(total))
    
    # Create the directory if need be
    if not os.path.isdir('out'): os.mkdir('out')

    # Save the maps        
    filename = "out/{}_epsilons.asc".format(prefix)
    print("\nSaving {}".format(filename))
    write_asc(ascHeader, epsilons, filename)
    filename = "out/{}_beta.asc".format(prefix)
    print("Saving {}".format(filename))
    write_asc(ascHeader, meanBeta, filename)


# Get the beta values that generate the PfPR for the given population and 
# treatment level, this function will start with the lowest epsilon value 
# and increase it until at least one value is found to be returned
def get_betas(zone, pfpr, population, treatment, lookup):
    global WARNINGS

    # Initial values
    epsilon = 0
    betas = []

    # Increase the epsilon until at least one value is found
    while len(betas) == 0:
        epsilon += EPSILON
        betas = get_betas_scan(zone, pfpr, population, treatment, lookup, epsilon)

        # Prevent an infinite loop, will result in an error
        if epsilon == MAX_EPSILON:
            print('Match not found!\nPfPR: ', pfpr, 'Population: ', population)
            return [None, None]

    # If the PfPR is zero then verify that beta returned will be zero
    if pfpr == 0 and sum(betas) > 0:
        # Resolve the bins
        populationBin = int(cl.get_bin(population, lookup[zone].keys()))
        treatmentBin = cl.get_bin(treatment, lookup[zone][populationBin].keys())

        # Append a warning for this bin if it hasn't already been added
        binning = "Zone: {}, Population: {}, Treatment: {}".format(zone, populationBin, treatmentBin)
        if binning not in WARNINGS:
            WARNINGS += '\nWARNING: Non-zero beta returned when PfPR is zero for bin = {}'.format(binning)
        return [[0], 0]

    # Return the results
    return [betas, epsilon] 


# Get the beta values that generate the PfPR for the given population and 
# treatment level, within the given margin of error.
def get_betas_scan(zone, pfpr, population, treatment, lookup, epsilon):

    # The zone is a bin, so it should just be there
    if not zone in lookup:
        raise ValueError("Zone {} was not found in lookup".format(zone))

    # Determine the population and treatment bin we are working with
    populationBin = cl.get_bin(population, lookup[zone].keys())
    treatmentBin = cl.get_bin(treatment, lookup[zone][populationBin].keys())
    
    # Note the bounds
    low = pfpr - epsilon
    high = pfpr + epsilon

    # Scan the PfPR values for the population and treatment level that are 
    # within the margin
    betas = []
    for value in lookup[zone][populationBin][treatmentBin]:

        # Add the value if it is in the bounds
        if low <= value[0] and value[0] <= high:
            betas.append(value[1])
            
        # We assume the data is stored, so break once the high value is less
        # than the current PfPR
        if high < value[0]: break

    # Return the betas collected
    return(betas)
    

# Main entry point for the script
def main(configuration, gisPath, studyId, useCache, age, pfpr):

    # Parse the country prefix
    prefix = cl.get_prefix(configuration)
    if prefix is None:
        sys.stderr.write("Invalid country code associated with configuration file: {}\n".format(configuration))
        sys.exit(1)

    # Load the configuration
    cfg = cl.load_configuration(configuration)

    # Attempt to load the data from the database, if that fails, fall back to local file
    try:
        if not useCache:
            cl.query_betas(cfg["connection_string"], studyId, age, filename = BETAVALUES)    
        else:
            print("Using cached calibration values for map...")
    except Exception as err:
        sys.stderr.write("ERROR: {}\n".format(str(err)))

    # Proceed with creating beta map
    create_beta_map(cfg, gisPath, prefix, age, pfpr)


if __name__ == "__main__":
    # Parse the parameters
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', action='store', dest='configuration', required=True, help='The configuration file to reference when creating the beta map')
    parser.add_argument('-g', action='store', dest='gis', required=True, help='The path to the directory that the GIS files can be found in')
    parser.add_argument('-s', action='store', dest='studyid', default=1, help='The id of the study to use for the reference beta values (default 1)')
    parser.add_argument('--age', action='store', dest='age', default='2-10', help='The age band to use for map generation, either 0-59 or 2-10 (default)')
    parser.add_argument('--pfpr', action='store', dest='pfpr', default=None, help='Override the default PfPR file with the one supplied')
    parser.add_argument('--cache', action='store_true', dest='useCache', help='Use cached values for the calibration')
    args = parser.parse_args()

    # Check to make sure the age band supplied is valid
    if args.age not in ['0-59', '2-10']:
        sys.stderr.write("The age band supplied is not valid, expected \'0-59\' or \'2-10\', got: {}\n".format(args.age))
        sys.exit(1)

    # Check that the study id is supplied if we are not using the cache
    if not args.useCache and args.studyid == -1:
        sys.stderr.write("The study id must be supplied when not using a cached calibration\n")
        sys.exit(1)
    
    # Call the main function with the parameters
    try:
        main(args.configuration, args.gis, int(args.studyid), args.useCache, args.age, args.pfpr)
    except Exception as err:
        sys.stderr.write("ERROR: {}\n".format(str(err)))
        sys.exit(1)
