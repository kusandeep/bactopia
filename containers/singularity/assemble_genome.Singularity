Bootstrap: docker
From: nfcore/base

%labels
    MAINTAINER Robert A. Petit III <robert.petit@emory.edu>
    DESCRIPTION Singularity image containing requirements for the Bactopia assemble_genome process
    VERSION 1.4.x

%environment
    PATH=/opt/conda/envs/bactopia-assemble_genome/bin:$PATH
    export PATH

%files
    conda/linux/assemble_genome.yml /

%post
    /opt/conda/bin/conda env create -f /assemble_genome.yml
    /opt/conda/bin/conda clean -a
