"""State-machine regression: instant opening, validation, cancellation, cached waveform."""
import sys, tempfile, threading, math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autoshibi import verification as v, pipeline as p, config
from autoshibi.events import Reporter,CancelToken,Cancelled

def main():
    job=config.default_job();job.update(mode='generate',derush=True,manual_verify=True)
    ids=[s[0] for s in p.stages_for(job)]
    assert ids.index('derush')<ids.index('verify')<ids.index('speech')<ids.index('poses')<ids.index('capcut')
    job['manual_verify']=False;assert 'verify' not in [s[0] for s in p.stages_for(job)]
    job.update(mode='edit',manual_verify=True);assert 'verify' not in [s[0] for s in p.stages_for(job)]
    for bad in ([],[[0,math.inf]],[[0,math.nan]],[[-1,2]],[[0,11]],[[1,1]],['bad']):
        try:v.validate_ranges(bad,10)
        except (ValueError,TypeError):pass
        else:raise AssertionError(bad)
    assert v.validate_ranges([[10,10+.04]],20) == [(10.,10.04)]
    assert v.validate_ranges([[1,1.001]],20) == [(1.,1.001)]
    assert v.validate_ranges([[0,1],[1,1.001],[1.001,2]],20) == [(0.,2.)]
    assert v.validate_ranges([[-.00001,10.00001]],10) == [(0.,10.)]
    started,release=threading.Event(),threading.Event()
    old=v._waveform
    def wave(*a):started.set();release.wait(5);return [0,.5,1]
    v._waveform=wave
    with tempfile.TemporaryDirectory() as d:
        token=CancelToken();rep=Reporter([('verify','Vérifier',1)]);out=[]
        t=threading.Thread(target=lambda:out.append(v.request([(0,2),(6,10)],[(3,4)],'source.mp4',10,[],d,rep,token)))
        t.start();assert started.wait(2)
        st=v.state();assert st['active'] and st['wave_status']=='loading'
        assert not v.decide('stale',[[0,10]])['ok']
        assert not v.decide(st['id'],[])['ok']
        assert v.decide(st['id'],[[0,1],[3,10]])['ok']
        t.join(1);assert not t.is_alive() and out==[[(0.,1.),(3.,10.)]]
        release.set()
        failed=[]
        def cancel_run():
            try:v.request([(0,2)],[],'source.mp4',10,[],d,Reporter([('verify','Vérifier',1)]),token)
            except Cancelled:failed.append(True)
        t=threading.Thread(target=cancel_run);t.start()
        import time
        time.sleep(.05);token.cancel();t.join(2)
        assert failed and not v.state()['active']
    v._waveform=old
    print('PASS: stage order, optional bypass, invalid ranges, stale session, nonblocking waveform, restoration/deletion, cancellation')
if __name__=='__main__':main()
