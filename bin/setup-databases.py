#! /usr/bin/env python3
"""
usage: setup-organism-databases.py [-h] [--mlst MLST] [--cgmlst CGMLST]
                                   [--ariba ARIBA] [--skip_prokka]
                                   [--include_genus] [--identity FLOAT]
                                   [--overlap FLOAT] [--max_memory INT]
                                   [--fast_cluster] [--cpus INT]
                                   [--clear_cache] [--force] [--force_ariba]
                                   [--force_mlst] [--force_prokka]
                                   [--keep_files] [--list_databases]
                                   [--depends] [--verbose] [--silent]
                                   OUTPUT_DIRECTORY

Setup default databases (MLST, resistance, virulence, annotation) for a given
organism.

positional arguments:
  OUTPUT_DIRECTORY  Directory to write output.

optional arguments:
  -h, --help        show this help message and exit

Sequence Typing:
  --mlst MLST       Download MLST schema for a given species or a list of
                    species in a text file.
  --cgmlst CGMLST   Download cgMLST schema for a given species or a list of
                    species in a text file.

Resistance/Virulence (Ariba):
  --ariba ARIBA     Setup Ariba database for a given database or a list of
                    databases in a text file. (Default: card,vfdb_core)

Custom Prokka Protein Database:
  --skip_prokka     Skip creation of a Prokka formatted fasta for each
                    organism
  --include_genus   Include all genus members in the Prokka database
  --identity FLOAT  CD-HIT (-c) sequence identity threshold. (Default: 0.9)
  --overlap FLOAT   CD-HIT (-s) length difference cutoff. (Default: 0.8)
  --max_memory INT  CD-HIT (-M) memory limit (in MB). (Default: unlimited
  --fast_cluster    Use CD-HIT's (-g 0) fast clustering algorithm, instead of
                    the accurate but slow algorithm.

Helpful Options:
  --cpus INT        Number of cpus to use. (Default: 1)
  --clear_cache     Remove any existing cache.
  --force           Forcibly overwrite existing databases.
  --force_ariba     Forcibly overwrite existing Ariba databases.
  --force_mlst      Forcibly overwrite existing MLST databases.
  --force_prokka    Forcibly overwrite existing Prokka databases.
  --keep_files      Keep all downloaded and intermediate files.
  --list_databases  List resistance/virulence Ariba databases and (cg)MLST
                    schemas available for setup.
  --depends         Verify dependencies are installed.

Adjust Verbosity:
  --verbose         Print debug related text.
  --silent          Only critical errors will be printed.
"""
import glob
import json
import logging
import os
import sys

from Bio import SeqIO
from bs4 import BeautifulSoup
from executor import ExternalCommand

STDOUT = 11
STDERR = 12
CACHE_DIR = f'{os.path.expanduser("~")}/.bactopia'
CACHE_JSON = f'{CACHE_DIR}/databases.json'
EXPIRATION = 15 # Refresh db info if cache is older than 15 days
logging.addLevelName(STDOUT, "STDOUT")
logging.addLevelName(STDERR, "STDERR")


def check_cache(clear_cache=False):
    """Check if a local cache exists to avoid re-downloads."""
    import time

    logging.debug(f'Checking for existing cache')
    if not os.path.exists(CACHE_DIR):
        logging.debug(f'Creating cache directory ({CACHE_DIR})')
        execute(f'mkdir -p {CACHE_DIR}')

    cache_data = {}
    if os.path.exists(CACHE_JSON):
        logging.debug(f'Found existing database cache ({CACHE_JSON})')
        days_old = (time.time() - os.path.getctime(CACHE_JSON)) // (24 * 3600)
        if days_old >= EXPIRATION or clear_cache:
            logging.debug((f'Deleting {CACHE_JSON}, Reason: older than '
                           f'{EXPIRATION} days or "--clear_cache" used'))
            execute(f'rm {CACHE_JSON}')
        else:
            with open(CACHE_JSON, 'r') as cache_fh:
                cache_data = json.load(cache_fh)

    return cache_data


def get_available_databases(clear_cache):
    """Get a list of available databases to be set up."""
    data = check_cache(clear_cache=clear_cache)
    expected = ['ariba', 'cgmlst', 'pubmlst']
    if sum([k in data for k in expected]) != len(expected):
        logging.debug((f'Existing database cache ({CACHE_JSON}) is missing '
                       'expected fields, refreshing.'))
        data = {
            'ariba': sorted(ariba_databases()),
            'cgmlst': sorted(cgmlst_schemas()),
            'pubmlst': pubmlst_schemas()
        }

        with open(CACHE_JSON, 'w') as cache_fh:
            logging.debug(f'Created database cache ({CACHE_JSON})')
            json.dump(data, cache_fh, indent=4, sort_keys=True)

    return [data['ariba'], data['pubmlst'], data['cgmlst']]


def validate_requirements():
    """Validate the required programs are available, if not exit (1)."""
    from shutil import which
    programs = {
        'ariba': which('ariba'), 'makeblastdb': which('makeblastdb'),
        'cd-hit': which('cd-hit'), 'wget': which('wget'),
        'unzip': which('unzip'), 'gzip': which('gzip')
        # 'mentalist': which('mentalist')
    }

    missing = False
    for prog, path in programs.items():
        if path:
            logging.debug(f'{prog}: command found.')
        else:
            logging.error(f'{prog}: command not found.')
            missing = True

    if missing:
        logging.error("Requirement missing, exiting")
        sys.exit(1)


def cgmlst_schemas():
    """For MentaLiST: Schemas available from www.cgmlst.org"""
    from urllib.request import urlopen
    soup = BeautifulSoup(urlopen('https://www.cgmlst.org/ncs'), "lxml")
    schemas = {}
    for link in soup.find_all('a'):
        address = link.get('href')
        # Example: https://www.cgmlst.org/ncs/schema/3956907/
        if 'schema' in address:
            schema_id = address.split('/')[-2]
            name = link.get_text().rstrip(" cgMLST")
            if "/" in name:
                genus, species = name.split()
                for s in species.split('/'):
                    schemas[f'{genus} {s}'] = schema_id
            else:
                schemas[name] = schema_id
    return schemas


def pubmlst_schemas():
    """Use Ariba to pull available MLST schemas from pubmlst.org"""
    schemas = {}
    query = execute('ariba pubmlstspecies', capture=True).rstrip().split('\n')
    for schema in query:
        schemas[schema] = schema

    # Map Mycobacterium species to 'Mycobacteria spp.'
    mycobacterium = [
        'Mycobacterium abscessus',
        'Mycobacterium africanum',
        'Mycobacterium avium',
        'Mycobacterium bovis',
        'Mycobacterium canettii',
        'Mycobacterium caprae',
        'Mycobacterium chubuense',
        'Mycobacterium colombiense',
        'Mycobacterium fortuitum',
        'Mycobacterium gilvum',
        'Mycobacterium hassiacum',
        'Mycobacterium indicus',
        'Mycobacterium intracellulare',
        'Mycobacterium kansasii',
        'Mycobacterium leprae',
        'Mycobacterium liflandii',
        'Mycobacterium mageritense',
        'Mycobacterium marinum',
        'Mycobacterium microti',
        'Mycobacterium orygis',
        'Mycobacterium parascrofulaceum',
        'Mycobacterium phlei',
        'Mycobacterium pinnipedii',
        'Mycobacterium rhodesiae',
        'Mycobacterium septicum',
        'Mycobacterium simiae',
        'Mycobacterium smegmatis',
        'Mycobacterium sp.',
        'Mycobacterium thermoresistibile',
        'Mycobacterium tuberculosis',
        'Mycobacterium tusciae',
        'Mycobacterium ulcerans',
        'Mycobacterium vaccae',
        'Mycobacterium vanbaalenii',
        'Mycobacterium xenopi',
        'Mycobacterium yongonense'
    ]
    for species in mycobacterium:
        schemas[species] = 'Mycobacteria spp.'

    return schemas


def ariba_databases():
    """Print a list of databases available with 'ariba getref'."""
    getref_usage = ' '.join([
        line.strip() for line in
        execute('ariba getref --help', capture=True).strip().split('\n')
    ])
    databases = getref_usage.split('of: ')[1].split(' outprefix')[0]
    return databases.split()


def list_databases(ariba, pubmlst, missing=False):
    """Print available MLST and cgMLST schemas, and exit."""
    print_to = sys.stderr if missing else sys.stdout
    print("Preconfigured Ariba databases available:", file=print_to)
    print("\n".join(sorted(ariba)), file=print_to)

    print("\nMLST schemas available from pubMLST.org:", file=print_to)
    print("\n".join(sorted(pubmlst.keys())), file=print_to)
    """
    Disabled until MentaLiST conda install is fixed
    print("\ncgMLST schemas available from cgMLST.org:", file=print_to)
    print("\n".join(sorted(cgmlst.keys())), file=print_to)
    """
    sys.exit(1 if missing else 0)


def setup_requests(request, available_databases, title, skip_check=False):
    """Return a list of setup requests."""
    databases = []
    if os.path.exists(request):
        with open(request, 'r') is handle:
            for line in handle:
                database = line.rstrip()
                if database in available_databases or skip_check:
                    databases.append(database)
                else:
                    logging.error(f'{data} is not available from {title}')
        return databases
    elif request in available_databases or skip_check:
        databases.append(request)
    else:
        logging.error(f'{request} is not available from {title}')
    return databases


def setup_ariba(request, available_databases, outdir, force=False,
                keep_files=False):
    """Setup each of the requested databases using Ariba."""
    requests = setup_requests(request, available_databases, 'ariba')
    if requests:
        ariba_dir = f'{outdir}/ariba'
        for request in requests:
            prefix = f'{ariba_dir}/{request}'
            if os.path.exists(prefix):
                if force:
                    logging.info(f'--force, removing existing {request} setup')
                    execute(f'rm -rf {prefix}')
                else:
                    logging.info(f'{request} ({prefix}) exists, skipping')
                    continue
            # Setup Ariba database
            logging.info(f'Setting up {request} Ariba database')
            fa = f'{prefix}.fa'
            tsv = f'{prefix}.tsv'
            execute(f'mkdir -p {ariba_dir}')
            with open(f'{prefix}-log.txt', 'w') as ariba_log:
                execute(
                    f'ariba getref {request} {request}',
                    stdout_file=ariba_log, stderr_file=ariba_log,
                    directory=ariba_dir
                )
            execute(f'ariba prepareref -f {fa} -m {tsv} {prefix}')
            execute(f'date -u +"%Y-%m-%dT%H:%M:%SZ" > {request}-updated.txt',
                    directory=prefix)

            # Clean up
            if not keep_files:
                execute(f'rm {fa} {tsv}')
            execute(f'mv {request}*.* {request}/', directory=ariba_dir)
    else:
        logging.info("No valid Ariba databases to setup, skipping")


def setup_mlst(request, available_databases, outdir, force=False):
    """Setup MLST databases for each requested schema."""
    import re
    requests = setup_requests(request, available_databases, 'pubMLST.org')
    bad_chars = [' ', '#', '/', '(', ')']
    if requests:
        for request in requests:
            species = re.sub(r'[ /()]', "-", request.lower())
            species = species.replace('--', '-').strip('-')
            mlst_dir = f'{outdir}/{species}/mlst'
            if os.path.exists(f'{mlst_dir}/mlst-updated.txt'):
                if force:
                    logging.info(f'--force, removing existing {request} setup')
                    execute(f'rm -rf {mlst_dir}')
                else:
                    logging.info((f'{request} MLST Schema ({mlst_dir}) exists'
                                  ', skipping'))
                    continue
            # Setup MLST database
            logging.info(f'Setting up MLST for {request}')
            execute(f'mkdir -p {mlst_dir}')

            # Ariba
            organism_request = available_databases[request]
            logging.info(f'Creating Ariba MLST database')
            ariba_dir = f'{mlst_dir}/ariba'
            execute(f'ariba pubmlstget "{organism_request}" {ariba_dir}')

            # BLAST
            logging.info(f'Creating BLAST MLST database')
            blast_dir = f'{mlst_dir}/blast'
            for fasta in glob.glob(f'{ariba_dir}/pubmlst_download/*.tfa'):
                output = os.path.splitext(fasta)[0]
                execute(f'makeblastdb -in {fasta} -dbtype nucl -out {output}')
            execute(f'mv {ariba_dir}/pubmlst_download {blast_dir}')

            # MentaLiST
            """
            logging.info(f'Creating MentaLiST MLST database')
            mentalist_dir = f'{mlst_dir}/mentalist'
            execute(f'mkdir -p {mentalist_dir}')
            execute((f'mentalist download_pubmlst -o mlst -k 31 -s "{request}"'
                     ' --db mlst.db'), directory=mentalist_dir)
            """

            # Finish up
            execute(f'date -u +"%Y-%m-%dT%H:%M:%SZ" > mlst-updated.txt',
                    directory=mlst_dir)
    else:
        logging.info("No valid MLST schemas to setup, skipping")


def process_cds(cds):
    """Look over the CDS attributes and return passing CDS."""
    header = None
    seq = None
    qualifiers = cds.keys()
    ec_number = ''
    gene = ''
    product = cds['product'][0]
    is_pseudo = ('pseudo' in qualifiers)
    is_hypothetical = (product.lower() == "hypothetical protein")
    if not is_pseudo and not is_hypothetical:
        if 'ec_number' in qualifiers:
            ec_number = cds['ec_number'][0]
        if 'gene' in qualifiers:
            gene = cds['gene'][0]

        if 'protein_id' in qualifiers:
            protein_id = cds['protein_id'][0]
        elif 'locus_tag' in qualifiers:
            protein_id = cds['locus_tag'][0]

        header = f'>{protein_id} {ec_number}~~~{gene}~~~{product}'
        seq = cds['translation'][0]

    return [header, seq]


def setup_prokka(request, available_databases, outdir, force=False,
                 include_genus=False, identity=0.9, overlap=0.8, max_memory=0,
                 fast_cluster=False, keep_files=False, cpus=1):
    """
    Setup a Prokka compatible protein fasta file based on completed genomes.

    Implemented similar approach as Thanh Lê's "make_prokka_db". Check out
    his version for a standalone implementation!
    Github Repo: https://github.com/thanhleviet/make_prokka_db
    """
    import gzip
    import re
    from statistics import median, mean
    requests = setup_requests(request, available_databases, 'pubMLST.org',
                              skip_check=True)
    if requests:
        for request in requests:
            species = re.sub(r'[ /()]', "-", request.lower())
            species = species.replace('--', '-').strip('-')
            prokka_dir = f'{outdir}/{species}/annotation'
            genome_sizes = []

            if os.path.exists(f'{prokka_dir}/proteins.faa'):
                if force:
                    logging.info(f'--force, delete existing {prokka_dir}')
                    execute(f'rm -rf {prokka_dir}')
                else:
                    logging.info((f'{prokka_dir} exists, skipping'))
                    continue

            # Setup Prokka proteins file
            logging.info(f'Setting up custom Prokka proteins for {request}')
            execute(f'mkdir -p {prokka_dir}')

            # Download completed genomes
            logging.info(f'Downloading completed genomes')
            genome_dir = f'{prokka_dir}/genomes'
            genus = ' '.join(request.split()[0:2])
            if include_genus:
                genus = genus.split()[0]
            execute((f'ncbi-genome-download bacteria --genus "{genus}" '
                     f'-l complete -o {prokka_dir}/genomes -F genbank '
                     f'-m {prokka_dir}/ncbi-metadata.txt -p {cpus}'))

            # Extract information from Genbank files
            genbank_files = execute(
                'find -name "*.gbff.gz"', directory=prokka_dir, capture=True
            ).split('\n')
            count = 0
            passing_cds = f'{prokka_dir}/passing-cds.faa'
            logging.info(f'Processing {len(genbank_files)-1} Genbank files')
            with open(passing_cds, 'w') as fasta_fh:
                for genbank in genbank_files:
                    if genbank:
                        sizes = []
                        genbank = genbank.replace('./', f'{prokka_dir}/')
                        with gzip.open(genbank, 'rt') as genbank_fh:
                            for record in SeqIO.parse(genbank_fh, 'genbank'):
                                # Aggregate chromosome and plasmids
                                sizes.append(len(record.seq))
                                for feature in record.features:
                                    if feature.type == 'CDS':
                                        header, seq = process_cds(
                                            feature.qualifiers
                                        )

                                        if header and seq:
                                            count += 1
                                            fasta_fh.write(f'{header}\n')
                                            fasta_fh.write(f'{seq}\n')

                        # Only add genome sizes for the species, incase the
                        # option '--inlude_genus' was used.
                        if record.annotations["organism"].startswith(request):
                            logging.debug(
                                f'Added {record.annotations["organism"]} '
                                f'({sum(sizes)}) to median genome size '
                                'calculation.'
                            )
                            genome_sizes.append(sum(sizes))
                        else:
                            logging.debug(
                                f'Skip adding {record.annotations["organism"]} '
                                f'({sum(sizes)}) to median genome size '
                                f'calculation (not {request}).'
                            )

            total_genome = len(genome_sizes)
            median_genome = int(median(genome_sizes))
            logging.info(
                f'Median genome size: {median_genome} (n={total_genome})'
            )
            cdhit_cds = f'{prokka_dir}/proteins.faa'
            logging.info(f'Running CD-HIT on {count} proteins')
            g = 0 if fast_cluster else 1
            execute((f'cd-hit -i {passing_cds} -o {cdhit_cds} -s {overlap} '
                     f'-g {g} -c {identity} -T {cpus} -M {max_memory}'))

            # Finish up
            with open(f'{prokka_dir}/genome_size.json', 'w') as genome_size_fh:
                gs_dict = {
                    'min': min(genome_sizes),
                    'median': int(median(genome_sizes)),
                    'mean': int(median(genome_sizes)),
                    'max': max(genome_sizes),
                    'total': total_genome,
                    'description': (
                        f'Genome size values are based on {total_genome} '
                        'completed genomes (RefSeq).'
                    )
                }
                json.dump(gs_dict, genome_size_fh, indent=4)
            execute(f'date -u +"%Y-%m-%dT%H:%M:%SZ" > proteins-updated.txt',
                    directory=prokka_dir)
            execute(f'grep -H -c "^>" *.faa > cdhit-stats.txt',
                    directory=prokka_dir)
            execute(f'sed -i "s=passing-cds.faa:=original\t=" cdhit-stats.txt',
                    directory=prokka_dir)
            execute(
                f'sed -i "s=proteins.faa:=after_cd-hit\t=" cdhit-stats.txt',
                directory=prokka_dir
            )

            # Clean up
            if not keep_files:
                execute(f'rm -rf {passing_cds} {genome_dir}/')
    else:
        logging.info("No valid organism to setup, skipping")


def setup_minmer(outdir, force=False):
    """Download precomputed Refseq (Mash) and Genbank (Sourmash) databases."""
    databases = {
        # Last updated: 2019-03-04
        'genbank-k21.json.gz': 'https://osf.io/d7rv8/download',
        'genbank-k31.json.gz': 'https://osf.io/4f8n3/download',
        'genbank-k51.json.gz': 'https://osf.io/nemkw/download',
        'refseq-k21-s1000.msh': (
            'https://obj.umiacs.umd.edu/mash/screen/RefSeq88n.msh.gz'
        )
    }

    minmer_dir = f'{outdir}/minmer'
    update_timestamp = False
    execute(f'mkdir -p {minmer_dir}')
    for filename, url in databases.items():
        filepath = f'{minmer_dir}/{filename}'
        if os.path.exists(filepath):
            if force:
                logging.info(f'--force, removing existing {filepath} setup')
                execute(f'rm -rf {filepath}')
                update_timestamp = True
            else:
                logging.info(f'{filepath} exists, skipping')
                continue
        execute(f'wget --quiet -O {filename} {url}', directory=minmer_dir)
    # Finish up
    if update_timestamp:
        execute(f'date -u +"%Y-%m-%dT%H:%M:%SZ" > minmer-updated.txt',
                directory=minmer_dir)


def setup_plsdb(outdir, keep_files=False, force=False):
    """Download precomputed PLSDB databases."""
    url = 'https://ccb-microbe.cs.uni-saarland.de/plsdb/plasmids/download/?zip'
    plsdb_dir = f'{outdir}/plasmid'
    if os.path.exists(plsdb_dir):
        if force:
            logging.info(f'--force, removing existing {plsdb_dir} setup')
            execute(f'rm -rf {plsdb_dir}')
        else:
            logging.info(f'{plsdb_dir} exists, skipping')
            return None

    execute(f'mkdir -p {plsdb_dir}')
    execute(f'wget --quiet -O plsdb.zip {url}', directory=plsdb_dir)
    execute('unzip plsdb.zip', directory=plsdb_dir)
    execute('ls > plsdb-orginal-names.txt', directory=plsdb_dir)

    # Rename files to generic prefix
    mash_file = os.path.basename(glob.glob(f'{plsdb_dir}/*.msh')[0])
    prefix = mash_file.replace('.msh', '')
    for plsdb_file in os.listdir(plsdb_dir):
        if plsdb_file.startswith(prefix):
            new_name = plsdb_file.replace(prefix, 'plsdb')
            execute(f'mv {plsdb_file} {new_name}', directory=plsdb_dir)

    # Clean up
    if not keep_files:
        execute('rm plsdb.zip', directory=plsdb_dir)

    # Finish up
    execute(f'date -u +"%Y-%m-%dT%H:%M:%SZ" > plsdb-updated.txt',
            directory=plsdb_dir)


def setup_cgmlst(request, available_databases, outdir, force=False):
    """Setup cgMLST databases for each requested schema."""
    requests = setup_requests(request, available_databases, 'cgmlst.org')
    if requests:
        for request in requests:
            species = request.lower().replace(' ', '_')
            cgmlst_dir = f'{outdir}/{species}/cgmlst'
            if os.path.exists(cgmlst_dir):
                if force:
                    logging.info(f'--force, removing existing {request} setup')
                    execute(f'rm -rf {cgmlst_dir}')
                else:
                    logging.info(f'{request} ({cgmlst_dir}) exists, skipping')
                    continue
            # Setup MLST database
            logging.info(f'Setting up xgMLST for {request}')
            execute(f'mkdir -p {cgmlst_dir}')

            # MentaLiST
            logging.info(f'Creating MentaLiST MLST database')
            mentalist_dir = f'{cgmlst_dir}/mentalist'
            execute(f'mkdir -p {mentalist_dir}')
            execute((
                f'mentalist download_cgmlst -o cgmlst -k 31 -s "{request}" '
                '--db cgmlst.db'
            ), directory=mentalist_dir)

            # Finish up
            execute(f'date -u +"%Y-%m-%dT%H:%M:%SZ" > {species}-updated.txt',
                    directory=cgmlst_dir)
    else:
        logging.info("No valid cgMLST schemas to setup, skipping")


def create_summary(outdir):
    """Create a summary of available databases in JSON format."""
    from collections import OrderedDict
    available_databases = OrderedDict()

    # Ariba
    available_databases['ariba'] = []
    for db in sorted(os.listdir(f'{outdir}/ariba')):
        available_databases['ariba'].append({
            'name': db,
            'last_update': execute(
                f'head -n 1 {outdir}/ariba/{db}/{db}-updated.txt', capture=True
            ).rstrip()
        })

    # Minmers
    available_databases['minmer'] = {
        'sketches': [],
        'last_update': execute(
            f'head -n 1 {outdir}/minmer/minmer-updated.txt', capture=True
        ).rstrip()
    }
    for sketch in sorted(os.listdir(f'{outdir}/minmer')):
        if sketch != 'minmer-updated.txt':
            available_databases['minmer']['sketches'].append(sketch)

    # PLSDB (plasmids)
    if os.path.exists(f'{outdir}/plasmid/plsdb-updated.txt'):
        available_databases['plasmid'] = {
            'sketches': 'plsdb.msh',
            'blastdb': 'plsdb.fna',
            'last_update': execute(
                f'head -n 1 {outdir}/plasmid/plsdb-updated.txt', capture=True
            ).rstrip()
        }

    # Organisms
    available_databases['organism-specific'] = OrderedDict()
    for organism in sorted(os.listdir(f'{outdir}/organism-specific')):
        new_organism = OrderedDict()
        new_organism['mlst'] = []
        organism_dir = f'{outdir}/organism-specific/{organism}'

        prokka = f'{organism_dir}/annotation'
        if os.path.exists(f'{prokka}/proteins.faa'):
            new_organism['annotation'] = {
                'proteins': f'organism-specific/{organism}/annotation/proteins.faa',
                'last_updated': execute(
                    f'head -n 1 {prokka}/proteins-updated.txt',
                    capture=True
                ).rstrip()
            }

        if os.path.exists(f'{prokka}/genome_size.json'):
            with open(f'{prokka}/genome_size.json', 'r') as gs_fh:
                json_data = json.load(gs_fh)
                print(json_data)
                new_organism['genome_size'] = json_data

        mlst = f'{organism_dir}/mlst'
        if os.path.exists(f'{mlst}/ariba/ref_db/00.auto_metadata.tsv'):
            new_organism['mlst'] = {
                'ariba': f'organism-specific/{organism}/mlst/ariba/ref_db',
                'blast': f'organism-specific/{organism}/mlst/blast',
                'last_updated': execute(
                    f'head -n 1 {mlst}/mlst-updated.txt', capture=True
                ).rstrip()
            }

        optionals = sorted([
            'insertion-sequences', 'reference-genomes', 'mapping-sequences',
            'blast'
        ])
        new_organism['optional'] = OrderedDict()
        for optional in optionals:
            # These are optional directories users can add data to
            optional_dir = f'organism-specific/{organism}/optional/{optional}'
            if not os.path.exists(optional_dir):
                execute(f'mkdir -p {optional_dir}', directory=outdir)
            if optional == 'blast':
                new_organism['optional'][optional] = [
                    f'{optional_dir}/genes',
                    f'{optional_dir}/primers',
                    f'{optional_dir}/proteins',
                ]
                for blast_dir in new_organism['optional'][optional]:
                    execute(f'mkdir -p {blast_dir}', directory=outdir)
            else:
                new_organism['optional'][optional] = f'{optional_dir}'

        available_databases['organism-specific'][organism] = new_organism

    with open(f'{outdir}/summary.json', 'w') as json_handle:
        logging.info(f'Writing summary of available databases')
        json.dump(available_databases, json_handle, indent=4)
        logging.debug(json.dumps(available_databases, indent=4))


def set_log_level(error, debug):
    """Set the output log level."""
    return logging.ERROR if error else logging.DEBUG if debug else logging.INFO


def get_log_level():
    """Return logging level name."""
    return logging.getLevelName(logging.getLogger().getEffectiveLevel())


def execute(cmd, directory=os.getcwd(), capture=False, stdout_file=None,
            stderr_file=None):
    """A simple wrapper around executor."""
    command = ExternalCommand(
        cmd, directory=directory, capture=True, capture_stderr=True,
        stdout_file=stdout_file, stderr_file=stderr_file
    )

    command.start()
    if get_log_level() == 'DEBUG':
        logging.log(STDOUT, command.decoded_stdout)
        logging.log(STDERR, command.decoded_stderr)

    if capture:
        return command.decoded_stdout


if __name__ == '__main__':
    import argparse as ap
    parser = ap.ArgumentParser(
        prog='setup-organism-databases.py',
        conflict_handler='resolve',
        description=('Setup default databases (MLST, resistance, virulence, '
                     'annotation) for a given organism.'))

    parser.add_argument(
        'outdir', metavar="OUTPUT_DIRECTORY", type=str,
        help='Directory to write output.'
    )

    group1 = parser.add_argument_group('Resistance/Virulence (Ariba)')
    group1.add_argument(
        '--ariba', metavar="ARIBA", type=str, default='card,vfdb_core',
        help=('Setup Ariba database for a given database or a list of '
              'databases in a text file. (Default: card,vfdb_core)')
    )

    group2 = parser.add_argument_group('Organisms')
    group2.add_argument(
        '--organism', metavar="MLST", type=str,
        help=('Download available (cg)MLST schemas and completed genomes for '
              'a given species or a list of species in a text file.')
    )

    group3 = parser.add_argument_group('Custom Prokka Protein Database')
    group3.add_argument(
        '--skip_prokka', action='store_true',
        help=('Skip creation of a Prokka formatted fasta for each organism')
    )
    group3.add_argument(
        '--include_genus', action='store_true',
        help=('Include all genus members in the Prokka database')
    )
    group3.add_argument(
        '--identity', metavar="FLOAT", type=float, default=0.9,
        help=('CD-HIT (-c) sequence identity threshold. (Default: 0.9)')
    )
    group3.add_argument(
        '--overlap', metavar="FLOAT", type=float, default=0.8,
        help=('CD-HIT (-s) length difference cutoff. (Default: 0.8)')
    )
    group3.add_argument(
        '--max_memory', metavar="INT", type=int, default=0,
        help=('CD-HIT (-M) memory limit (in MB). (Default: unlimited')
    )
    group3.add_argument(
        '--fast_cluster', action='store_true',
        help=("Use CD-HIT's (-g 0) fast clustering algorithm, instead of the "
              "accurate but slow algorithm.")
    )


    group4 = parser.add_argument_group('Minmer Databases/Sketches')
    group4.add_argument(
        '--skip_minmer', action='store_true',
        help='Skip download of pre-computed minmer datbases (mash, sourmash)'
    )

    group5 = parser.add_argument_group('PLSDB (Plasmid) Database/Sketch')
    group5.add_argument(
        '--skip_plsdb', action='store_true',
        help='Skip download of pre-computed PLSDB datbases (blast, mash)'
    )

    group6 = parser.add_argument_group('Helpful Options')
    group6.add_argument(
        '--cpus', metavar="INT", type=int, default=1,
        help=('Number of cpus to use. (Default: 1)')
    )
    group6.add_argument('--clear_cache', action='store_true',
                        help='Remove any existing cache.')

    group6.add_argument('--force', action='store_true',
                        help='Forcibly overwrite existing databases.')
    group6.add_argument('--force_ariba', action='store_true',
                        help='Forcibly overwrite existing Ariba databases.')
    group6.add_argument('--force_mlst', action='store_true',
                        help='Forcibly overwrite existing MLST databases.')
    group6.add_argument('--force_prokka', action='store_true',
                        help='Forcibly overwrite existing Prokka databases.')
    group6.add_argument('--force_minmer', action='store_true',
                        help='Forcibly overwrite existing minmer databases.')
    group6.add_argument('--force_plsdb', action='store_true',
                        help='Forcibly overwrite existing PLSDB databases.')
    group6.add_argument(
        '--keep_files', action='store_true',
        help=('Keep all downloaded and intermediate files.')
    )
    group6.add_argument(
        '--list_databases', action='store_true',
        help=('List resistance/virulence Ariba databases and (cg)MLST schemas '
              'available for setup.')
    )
    group6.add_argument('--depends', action='store_true',
                        help='Verify dependencies are installed.')

    group7 = parser.add_argument_group('Adjust Verbosity')
    group7.add_argument('--verbose', action='store_true',
                        help='Print debug related text.')
    group7.add_argument('--silent', action='store_true',
                        help='Only critical errors will be printed.')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # Setup logs
    FORMAT = '%(asctime)s:%(name)s:%(levelname)s - %(message)s'
    logging.basicConfig(format=FORMAT, datefmt='%Y-%m-%d %H:%M:%S',)
    logging.getLogger().setLevel(set_log_level(args.silent, args.verbose))
    if args.depends:
        validate_requirements()
        sys.exit(0)
    else:
        validate_requirements()

    ARIBA, PUBMLST, CGMLST = get_available_databases(args.clear_cache)
    if args.list_databases:
        list_databases(ARIBA, PUBMLST, CGMLST)

    if args.ariba:
        logging.info('Setting up Ariba databases')
        for database in args.ariba.split(','):
            setup_ariba(
                database, ARIBA, args.outdir, keep_files=args.keep_files,
                force=(args.force or args.force_ariba)
            )
    else:
        logging.info('No requests for an Ariba database, skipping')

    if not args.skip_minmer:
        logging.info('Setting up pre-computed Genbank/Refseq minmer databases')
        setup_minmer(args.outdir, force=(args.force or args.force_minmer))
    else:
        logging.info('Skipping minmer database step')

    if not args.skip_plsdb:
        logging.info('Setting up pre-computed PLSDB (plasmids) databases')
        setup_plsdb(args.outdir, keep_files=args.keep_files,
                    force=(args.force or args.force_plsdb))
    else:
        logging.info('Skipping PLSDB (plasmids) database step')

    # Organism databases
    if args.organism:
        organism_dir = f'{args.outdir}/organism-specific'
        logging.info('Setting up MLST databases')
        setup_mlst(args.organism, PUBMLST, organism_dir,
                   force=(args.force or args.force_mlst))

        if not args.skip_prokka:
            logging.info('Setting up custom Prokka proteins')
            setup_prokka(
                args.organism, PUBMLST, organism_dir, cpus=args.cpus,
                include_genus=args.include_genus, identity=args.identity,
                overlap=args.overlap, max_memory=args.max_memory,
                fast_cluster=args.fast_cluster, keep_files=args.keep_files,
                force=(args.force or args.force_prokka)
            )
        else:
            logging.info('Skipping custom Prokka database step')
        # logging.info('Setting up cgMLST databases')
        # Need mentalist conda install to be fixed
        # setup_cgmlst(args.organism, CGMLST, args.outdir, force=args.force)
    else:
        logging.info('No requests for an organism, skipping')

    create_summary(args.outdir)
