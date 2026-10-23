from app.integrations.prod.lead_intelligence_6 import *

def test_all_27_capabilities():
    assert len(capabilities()) == 27
    assert 'multidimensional_lead_score' in capabilities()
    assert 'contact_verification_mesh' in capabilities()

def test_score_gate_and_delivery():
    lead={'industry':'med spa','email':'owner@example.com','email_status':'valid','phone':'+14155552671','phone_verified':True,'data_quality_score':90,'intent_score':80,'why_now':'opened new location','owner_name':'Jane Doe'}
    r=evaluate_lead(lead, {'industries':['med spa']})
    assert r['scores']['overall'] > 0
    assert r['gate']['eligible'] is True
    assert r['delivery']['action'] in {'deliver_now','deliver'}

def test_conflict_resolution():
    r=field_confidence('phone',[{'value':'+1','source_type':'public_web','verified':False},{'value':'+2','source_type':'verified_api','verified':True}])
    assert r['conflict'] is True
    assert r['value']=='+2'

def test_state_machine_is_strict():
    assert transition('SCORED','gate',gate_passed=True)=='GATED'
    try:
        transition('DISCOVERED','deliver')
        assert False
    except ValueError:
        pass

def test_negative_and_quality_credit():
    lead={'opted_out':True,'email_status':'invalid'}
    assert negative_signals(lead)
    q=quality_guarantee(lead,True,'invalid')
    assert q['eligible_for_credit'] is True

def test_provider_order_cost_aware():
    providers=[{'capabilities':['email'],'field_hit_rate':.8,'field_accept_rate':.8,'cost':1,'latency_ms':100},{'capabilities':['email'],'field_hit_rate':.9,'field_accept_rate':.9,'cost':5,'latency_ms':100}]
    assert cost_aware_provider_order(providers,'email',{'lead_value_score':80})[0] is providers[0]

def test_stopping_and_calibration():
    assert should_keep_searching({'data_quality_score':20,'lead_value_score':80},1,10)['continue'] is True
    c=calibrate_scores([{'score':85,'positive':True},{'score':20,'positive':False}])
    assert c['80-100']['positive_rate']==1

def test_contact_verification_free_first_catalog():
    from app.integrations.prod.contact_verification_mesh import verification_catalog
    c=verification_catalog()
    assert c['email'][0]['tier'] == 0
    assert c['email'][1]['tier'] == 0
    assert c['phone'][0]['tier'] == 0
    assert c['phone'][1]['tier'] == 0
    assert any(x['name']=='hunter' and x['tier']==1 for x in c['email'])
    assert any(x['name']=='veriphone' and x['tier']==1 for x in c['phone'])
    assert any(x['name']=='kickbox' and x['tier']==2 for x in c['email'])
    assert any(x['name']=='ipqs' and x['tier']==2 for x in c['phone'])
