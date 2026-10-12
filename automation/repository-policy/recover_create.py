"""Acceptance-only recovery for a successfully applied single private pilot."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

spec=importlib.util.spec_from_file_location('live',Path(__file__).with_name('live_create.py'))
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)
p=live.policy


def verify_run(run, jobs, run_id, sha):
    p.require(run.get('id') == run_id and run.get('head_sha') == sha and
              run.get('repository',{}).get('full_name') == 'tyler180/github-infra' and
              run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main' and
              run.get('status') == 'completed', 'Applied run identity differs')
    expected='tyler180/github-workflows/.github/workflows/repository-create-deploy.yml@v2.1.0-rc.3'
    p.require(any(w.get('path') == expected and w.get('sha') == '6cfe1a724fbc1612e300cf321ae0bf6c73ce8ee6' for w in run.get('referenced_workflows',[])), 'Unexpected applied workflow revision')
    deploy=[j for j in jobs if j.get('name') == 'deploy / deploy']
    p.require(len(deploy)==1, 'Missing unique original deploy job')
    steps={s['name']:s.get('conclusion') for s in deploy[0].get('steps',[])}
    for name in ('Authorize exact plan after production approval','Apply authorized saved plan'):
        p.require(steps.get(name)=='success', 'Original authorization/apply did not succeed')


def verify_state(state, inventory, expected_id):
    p.require(state.get('version')==4, 'Unexpected state format')
    resources=state.get('resources',[])
    p.require(len(resources)==3 and all(r.get('mode')=='managed' for r in resources), 'Unexpected recovery state ownership')
    guard=[r for r in resources if r.get('type')=='terraform_data' and r.get('name')=='contract' and not r.get('module')]
    repo=[r for r in resources if r.get('type')=='github_repository' and r.get('name')=='this' and r.get('module')=='module.repositories["pilot"]']
    branch=[r for r in resources if r.get('type')=='github_branch_default' and r.get('name')=='this' and r.get('module')=='module.repositories["pilot"]']
    p.require(len(guard)==len(repo)==len(branch)==1 and all(len(r.get('instances',[]))==1 for r in resources), 'Unexpected recovery resources')
    attrs=lambda r:r['instances'][0]['attributes']
    p.require(attrs(guard[0]).get('input',{}).get('value')==inventory, 'State inventory differs from applied configuration')
    p.require(attrs(repo[0]).get('repo_id')==expected_id and attrs(repo[0]).get('name')=='github-module-pilot', 'State repository identity differs')
    p.require(attrs(branch[0]).get('repository')=='github-module-pilot' and attrs(branch[0]).get('branch')=='main', 'State default branch differs')


def main():
    parser=argparse.ArgumentParser()
    for name in ('configuration','applied-sha','current-sha','policy-sha','policy-version','state','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--repository-id',required=True,type=int)
    args=parser.parse_args()
    try:
        p.require(all(p.SHA.fullmatch(s) for s in (args.applied_sha,args.current_sha,args.policy_sha)), 'Full recovery revisions required')
        p.require(args.run_id==38320705436 and args.repository_id==1416221483, 'Recovery is restricted to the reviewed pilot apply')
        p.require(live.command(['gh','api','repos/tyler180/github-infra/commits/main','--jq','.sha']).decode().strip()==args.current_sha, 'Recovery superseded')
        live.verify_environment()
        run_env={**os.environ,'GH_TOKEN':os.environ['GH_RUN_TOKEN']}
        run=json.loads(live.command(['gh','api',f'repos/tyler180/github-infra/actions/runs/{args.run_id}'],env=run_env))
        jobs=json.loads(live.command(['gh','api',f'repos/tyler180/github-infra/actions/runs/{args.run_id}/jobs?per_page=100'],env=run_env))['jobs']
        verify_run(run,jobs,args.run_id,args.applied_sha)
        config=Path(args.configuration)
        p.require(live.command(['git','-C',str(config),'rev-parse','HEAD']).decode().strip()==args.applied_sha, 'Recovery configuration revision differs')
        inventory=p.load_json(config/'repositories.json')
        repos=live.validate_scope(inventory)
        p.require(set(repos)=={'pilot'} and repos['pilot'].get('name')=='github-module-pilot' and repos['pilot'].get('visibility')=='private', 'Only reviewed pilot is recoverable')
        state=p.load_json(args.state)
        verify_state(state,inventory,args.repository_id)
        existing,_=live.read_record(Path(args.output).parent)
        p.require(existing is None, 'Recovery cannot overwrite accepted history')
        entries=live.verify_repositories(repos)
        p.require(entries['pilot']['repository_id']==args.repository_id, 'Live repository identity differs')
        record={'schema_version':1,'configuration_sha':args.applied_sha,'inventory':inventory,
                'inventory_digest':p.digest(p.canonical(inventory)),'repositories':entries,
                'accepted_at':datetime.now(timezone.utc).isoformat(),
                'authorization':{'mode':'live-create-recovery','applied_run_id':args.run_id,
                    'policy_sha':args.policy_sha,'policy_version':args.policy_version,
                    'configuration_digest':p.tree_digest(config),'state_digest':p.digest(Path(args.state).read_bytes())}}
        output=Path(args.output);output.write_text(json.dumps(record))
        live.command(['aws','s3api','put-object','--bucket',live.BUCKET,'--key',live.KEY,'--body',str(output),'--if-none-match','*'])
        print('Verified completed pilot apply; initial accepted record recovered. No Terraform apply performed.')
        return 0
    except (p.PolicyError,KeyError,ValueError,OSError,TypeError) as error:
        reason=str(error) if isinstance(error,p.PolicyError) else type(error).__name__
        print('Acceptance recovery failed: '+reason)
        return 1

if __name__=='__main__':raise SystemExit(main())
