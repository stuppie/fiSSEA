

'''
November 21, 2014
Functional Impact SNP Set Enrichment Analysis (fiSSEA)
Authors: Erick R. Scott, Adam Mark, Ali Torkamani, Chunlei Wu, Andrew Su
The Scripps Research Institute

'''
#Libraries
import os, sys, time, gc, argparse
import json, httplib2
import pandas as pd
from pandas.io.json import json_normalize
import numpy as np
import cPickle, subprocess

#fiSSEA Libraries
sys.path.append('../pandasVCF/src/single_sample/')
from pdVCFsingle import *

sys.path.append('../myvariant/src/')
import myvariant







def myvariant_post(hgvs_list):
    '''
    Query and Parser for myvariant.info
    Parses Raw Elastic Search results into
    pandas dataframe
    
    Parameters
    -------------
    hgvs_list: list, required
        
        
    Output
    -------------
    pandas df:
        normalized json of myvariant results
    '''
    

    
    if type(hgvs_list) == list:
        hgvs_list = ','.join(hgvs_list)
        
    assert type(hgvs_list) == str
    
    con = mv.getvariants(hgvs_list, fields='dbnsfp.cadd.phred,dbnsfp.genename')    

    mv_df = json_normalize(con)
    mv_df.index = mv_df['_id']
    

    return mv_df
    
    


def get_myvariant_snp_annot(vcf_df_mv):
    '''
    Returns myvariant.info annotations for all snps for
    input vcf pandas dataframe. Submits myvariant.info
    POST request in snp batches of 1000.
    
    Parameters
    -------------
    vcf_df_mv: pandas df; required
    
        vcf_df_mv must contain the following columns:
            vartype1, vartype2, CHROM, REF, POS, a1, a2
            *please see pandasVCF docs for column definitions
            

    Output
    -------------
    myvariant.info results: pandas df
    
    '''
    

    
 
    if 'CHROM' not in vcf_df_mv.columns:
        vcf_df_mv.reset_index(inplace=True)
        
    
    vcf_df_mv = vcf_df_mv[(vcf_df_mv['vartype1'] == 'snp') | (vcf_df_mv['vartype2'] == 'snp')] #restrict to snps

    non_ref_a1 = vcf_df_mv[vcf_df_mv['REF'] != vcf_df_mv['a1']][['CHROM', 'POS', 'REF', 'a1']]  #restrict a1 to non-reference alleles
    hgvs_a1 = 'chr' + non_ref_a1['CHROM'].astype(str) + ":g." + non_ref_a1['POS'].astype(str)  \
                                 + non_ref_a1['REF'] + '>' +  non_ref_a1['a1']

    non_ref_a2 = vcf_df_mv[vcf_df_mv['REF'] != vcf_df_mv['a2']][['CHROM', 'POS', 'REF', 'a2']]  #restrict a2 to non-reference alleles
    hgvs_a2 = 'chr' + non_ref_a2['CHROM'].astype(str) + ":g." + non_ref_a2['POS'].astype(str) \
                                 + non_ref_a2['REF'] + '>' +  non_ref_a2['a2']


    hgvs_ids = set(hgvs_a1.values) | set(hgvs_a2.values)  #set of non-reference a1 and a2 snps

    myvariant_results = map(myvariant_post, hgvs_ids)
        
    return myvariant_results
    





class fiSSEA(object):

    
    def __init__(self, vcf_file_path, sample_id='', chunksize=100000, fiSSEA_score='dbnsfp.cadd.phred', gene_stat=np.mean):
        
        
        #setting fiSSEA_score
        self.fiSSEA_score = fiSSEA_score
        
        #setting gene statistic
        self.gene_stat = gene_stat
        
        #open Vcf object
        vcf_df_obj = Vcf(vcf_file_path, sample_id, ['#CHROM', 'POS', 'REF', 'ALT', 'ID', 'FILTER', 'FORMAT'], chunksize=chunksize)

        #read in vcf chunk
        vcf_df_obj.get_vcf_df_chunk()

        #drop duplicate vcf lines, if necessary
        vcf_df_obj.df = vcf_df_obj.df.drop_duplicates(subset=['CHROM', 'POS'])

        #remove empty variant lines
        vcf_df_obj.df = vcf_df_obj.df[vcf_df_obj.df.ALT!='.']

        #replace chr if necessary, depends on format of annotations
        vcf_df_obj.df['CHROM'] = vcf_df_obj.df['CHROM'].str.replace('chr', '')

        #parse vcf lines
        vcf_df_obj.add_variant_annotations( inplace=True, verbose=True)
        
        #settting vcf_df as class object
        self.vcf_df = vcf_df_obj.df

        #get myvariant.info annotations, create fiSSEA dataframe
        self.fiSSEA_status = self.get_fi_scores()
        
        #setting sample_id
        self.sample_id = sample_id
        

    
    def get_multigene_var_records(self, df_line):
        '''
        Splits multigene variants into separate
        rows
        '''
        genes = df_line['dbnsfp.genename']
        gene_series = [] #list of gene variants
        for gene in genes: #iterate through all genes intersecting this variant
            df_line_copy = df_line.copy()  #avoids aliasing
            del df_line_copy['dbnsfp.genename']  #removing genename to allow gene insertion
            df_line_copy['dbnsfp.genename'] = gene
            gene_series.append(df_line_copy)

        return gene_series
    
    
    
    def get_fi_scores(self):
        '''
        Creates fi_scores series, index on gene symbol, value fi_score.
        '''
        
        #Retrieving myvariant.info annotations for all snps
        myvariant_results = get_myvariant_snp_annot(self.vcf_df) #query and parse myvariant.info using all snps in self.vcf_df
        self.mv_annot = pd.concat(myvariant_results)  #setting dataframe of parsed myvariant.info results

        #reducing size of dataframe for fiSSEA
        fiSSEA_cols = ['dbnsfp.genename', self.fiSSEA_score]
        mv_annot_fiSSEA = self.mv_annot[fiSSEA_cols] 
        
        #dropping variants without a genename & filling fi_score NaN with 0
        mv_annot_fiSSEA.dropna(subset=['dbnsfp.genename'], inplace=True)  
        mv_annot_fiSSEA[self.fiSSEA_score].fillna(value=0)  

        
        #Splitting variants intersecting multiple genes into separate rows
        mv_annot_fiSSEA['dbnsfp.genename_type'] = mv_annot_fiSSEA['dbnsfp.genename'].map(type)
        mv_annot_fiSSEA_multigene = mv_annot_fiSSEA[mv_annot_fiSSEA['dbnsfp.genename_type']==list]
        mv_annot_fiSSEA = mv_annot_fiSSEA[~mv_annot_fiSSEA.index.isin(mv_annot_fiSSEA_multigene.index)]
        
        if len(mv_annot_fiSSEA_multigene) > 0:
            mv_annot_fiSSEA_multigene['index'] = mv_annot_fiSSEA_multigene.index
            mv_annot_fiSSEA_multigene.drop_duplicates(subset='index', inplace=True)
            mv_annot_fiSSEA_multigene = mv_annot_fiSSEA_multigene.apply(self.get_multigene_var_records, axis=1)
            mv_annot_fiSSEA_multigene = [n for i in mv_annot_fiSSEA_multigene for n in i]
            mv_annot_fiSSEA = mv_annot_fiSSEA.append(pd.DataFrame(mv_annot_fiSSEA_multigene))
            del mv_annot_fiSSEA_multigene
        
        gc.collect()

        
        #Setting gene specific functional impact score
        self.fi_scores = mv_annot_fiSSEA.groupby('dbnsfp.genename')[self.fiSSEA_score].aggregate(self.gene_stat)
        
        return 0


    def write_rnk_file(self):
            '''
            Writes functional impact scores to .rnk file in
            the fiSSEA/gsea/rnk/ directory.
    
            Returns path to rnk file
            '''
    
            fiSSEA_results_write = self.fi_scores.copy()
            fiSSEA_results_write.name = '#' + fiSSEA_results_write.name 
    
    
            rnk_path = self.rnk_write_dir + self.sample_id + self.fi_scores.name +'.rnk'
            self.rnk_path = rnk_path
            
            if not os.path.exists(self.rnk_write_dir):
                    os.makedirs(self.rnk_write_dir)
            
            fiSSEA_results_write.to_csv(self.rnk_path, sep='\t')
            return True
        
        
        
    def call_preranked_gsea(self):
        '''
        Calls GSEA PreRanked Analysis using several default parameters
        '''
    
        gsea_cmd = ['java', '-cp', self.gsea_jar_path, '-Xmx512m', 'xtools.gsea.GseaPreranked', \
                '-gmx', self.tissue_geneset_path, '-collapse false', '-mode Max_probe', \
                 '-nperm 1000', '-rnk', self.rnk_path, '-scoring_scheme classic', \
                '-rpt_label my_analysis', '-include_only_symbols true', \
                '-rpt_label', self.sample_id,  '-chip gseaftp.broadinstitute.org://pub/gsea/annotations/GENE_SYMBOL.chip', \
                '-include_only_symbols true', '-make_sets true', '-plot_top_x 20', \
                '-rnd_seed timestamp', '-set_max 15000', '-set_min 15', '-zip_report false', \
                '-out', self.output_dir_path, '-gui false']
    
    
        p = subprocess.Popen(gsea_cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    
        run_stdout = [">>> " + line.rstrip() for line in iter(p.stdout.readline, b'')]
        return run_stdout
    
    
    
    def run_GSEA_preranked(self, gsea_jar_path, tissue_geneset_path, output_dir_path):
        '''
        Calls GSEA jar for a PreRanked analysis.
        
        Parameters
        -------------
        gsea_jar_path: str, required
            Specifies the path to GSEA jar, e.g. /Users/bin/gsea/gsea2-2.1.0.jar
                Can be downloaded from http://www.broadinstitute.org/gsea/downloads.jsp
                
        rnk_write_dir: str, required
            Specifies the directory path where the rnk file should be written
                e.g. /Users/data/gsea/rnk/
        
        tissue_geneset_path: str, required
            Specifies the path to the tissue genesets (.gmt format)
                e.g.  /Users/data/gsea/genesets/EnrichmentValues_Primary_U133A_ave_percent_over70percent_no_brain.gmt
        
        output_dir_path: str, required
            Specifies the directory path where the GSEA output should be written
                e.g. /Users/data/gsea/results/
        
        Returns
        -------------
        
        '''
        
        #Setting GSEA parameter paths
        self.gsea_jar_path = gsea_jar_path
        self.rnk_write_dir = output_dir_path
        self.tissue_geneset_path = tissue_geneset_path
        self.output_dir_path = output_dir_path
        
        #Writing the rnk file to disk
        self.write_rnk_file()
        
        #Run PreRanked Gene Set Enrichment Analysis, please see run_preranked_gsea function to change
        #parameters
        #Returns 0 if successful run
        run_stdout = self.call_preranked_gsea()
        self.run_stdout = run_stdout

        
        last_written_dir = os.popen('ls -lht ' + self.output_dir_path).readlines()
        last_written_dir = last_written_dir[1].split()[-1]
        self.last_written_dir = last_written_dir  #likely the path to the output files
        
        return 0

    
    def open_GSEA_html(self):
        '''
        Polls the user-specified output directory for the last written
        file and opens the index.html file in that directory
        '''
        os.system('open ' + self.output_dir_path + self.last_written_dir + '/index.html')
        return 0





parser = argparse.ArgumentParser()
parser.add_argument('-i','--vcf_input', type=str,help='Specifies the input coding snp vcf file, /path/to/cds.vcf')
parser.add_argument('-o','--output_path', type=str,help='Specifies the output file path, e.g. /path/to/output/')
parser.add_argument('-s','--sample_id', type=str,help='Specifies the sample identifier in the vcf file, e.g. NA12878')
parser.add_argument('-c','--chunk', type=str,help='Specifies the chunksize for vcf iteration') 
parser.add_argument('-f','--fi_score', type=str,help='Specifies the name of the functional impact score, e.g. dbnsfp.cadd.phred') 
parser.add_argument('-g','--gene_stat', type=str,help='Specifies the gene statistic, e.g. np.mean')

parser.add_argument('-G','--gsea_jar', type=str,help='Specifies the path to the gsea jar, e.g. /path/to/gsea/gsea2-2.1.0.jar')
parser.add_argument('-T','--tissue_geneset', type=str,help='Specifies the path to the tissue geneset, e.g. /path/to/gsea/geneset/tissue.gmt')
parser.add_argument('-P','--pickle_path', type=str,help='Specifies the path to write pkl fiSSEA object, e.g. /path/to/pkl/sample_id.pkl')

opts = parser.parse_known_args()
vcf_path, output_path, sample_id = opts[0].vcf_input, opts[0].output_path, opts[0].sample_id 
fi_score, gene_statistic, chunk = opts[0].fi_score, opts[0].gene_stat, opts[0].chunk
gsea_jar_path, tissue_geneset_path, pkl_path = opts[0].gsea_jar, opts[0].tissue_geneset, opts[0].pickle_path

mv = myvariant.MyVariantInfo()



if __name__ == '__main__':
    
    
    if not fi_score:
        fi_score = 'dbnsfp.cadd.phred'
        
    if not gene_statistic:
        gene_statistic = np.mean
    
    if not chunk:
        chunk = 100000
    


    fiSSEA_results = fiSSEA(vcf_path, sample_id=sample_id, chunksize=chunk, fiSSEA_score=fi_score, gene_stat=gene_statistic)
    
    fiSSEA_results.run_GSEA_preranked(gsea_jar_path, tissue_geneset_path, output_path)
    

    
    if not pkl_path:
        pass
    else:
        cPickle.dump(fiSSEA_results, open(pkl_path, 'wb'))


#EXAMPLE COMMAND
#python fiSSEA.py -i ~/git/fiSSEA/data/snyderome_chr6_cds.vcf.gz -o ~/Desktop/output/ -s TB0001907 -G ~/git/fiSSEA/gsea/gsea2-2.1.0.jar -T ~/git/fiSSEA/gsea/genesets/EnrichmentValues_Primary_U133A_ave_percent_over70percent_no_brain.gmt -P ~/Desktop/output/testing.pkl
 


    