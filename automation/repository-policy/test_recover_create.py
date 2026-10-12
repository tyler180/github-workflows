import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('recover',Path(__file__).with_name('recover_create.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
SHA='a'*40

class RecoveryTests(unittest.TestCase):
    def run_fixture(self):
        return {'id':38320705436,'head_sha':SHA,'repository':{'full_name':'tyler180/github-infra'},
                'event':'workflow_dispatch','head_branch':'main','status':'completed',
                'referenced_workflows':[{'path':'tyler180/github-workflows/.github/workflows/repository-create-deploy.yml@v2.1.0-rc.3','sha':'6cfe1a724fbc1612e300cf321ae0bf6c73ce8ee6'}]}
    def jobs(self):
        return [{'name':'deploy / deploy','steps':[{'name':name,'conclusion':'success'} for name in
                 ('Authorize exact plan after production approval','Apply authorized saved plan')]}]
    def test_proven_successful_apply_is_required(self):
        r.verify_run(self.run_fixture(),self.jobs(),38320705436,SHA)
        jobs=self.jobs();jobs[0]['steps'][1]['conclusion']='failure'
        with self.assertRaises(r.p.PolicyError):r.verify_run(self.run_fixture(),jobs,38320705436,SHA)
    def test_wrong_source_or_workflow_rejected(self):
        for key,value in [('head_sha','b'*40),('head_branch','dev'),('referenced_workflows',[])]:
            run=self.run_fixture();run[key]=value
            with self.assertRaises(r.p.PolicyError):r.verify_run(run,self.jobs(),38320705436,SHA)
    def state(self):
        inventory={'schema_version':1,'repositories':{'pilot':{'name':'github-module-pilot','stage':'create','visibility':'private'}}}
        def resource(kind,name,attrs,module=None):
            return {'type':kind,'name':name,'mode':'managed','module':module,'instances':[{'attributes':attrs}]}
        state={'version':4,'resources':[
            resource('terraform_data','contract',{'input':{'value':inventory}}),
            resource('github_repository','this',{'repo_id':1416221483,'name':'github-module-pilot'},'module.repositories["pilot"]'),
            resource('github_branch_default','this',{'repository':'github-module-pilot','branch':'main'},'module.repositories["pilot"]')]}
        return inventory,state
    def test_matching_state_passes(self):
        inventory,state=self.state();r.verify_state(state,inventory,1416221483)
    def test_wrong_id_inventory_or_extra_resource_rejected(self):
        inventory,state=self.state()
        for change in ('id','inventory','extra'):
            bad=copy.deepcopy(state)
            if change=='id':bad['resources'][1]['instances'][0]['attributes']['repo_id']=42
            if change=='inventory':bad['resources'][0]['instances'][0]['attributes']['input']['value']={}
            if change=='extra':bad['resources'].append(copy.deepcopy(bad['resources'][1]))
            with self.assertRaises(r.p.PolicyError):r.verify_state(bad,inventory,1416221483)

if __name__=='__main__':unittest.main()
