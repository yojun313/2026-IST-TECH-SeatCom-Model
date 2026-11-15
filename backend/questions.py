QUESTION_DEFS = [
    {'id':'outlet','order':1,'question':'노트북을 주로 사용하시나요? (또는 주로 콘센트가 필요하신가요?)',
     'options':[{'label':'네','value':'required'},{'label':'아니오','value':'not_required'}]},
    {'id':'hvac_proximity','order':2,'question':'에어컨과 가까운 것을 선호하시나요?',
     'options':[{'label':'네','value':'near'},{'label':'아니오','value':'not_near'},{'label':'상관없음','value':'no_preference'}]},
    {'id':'noise_sensitivity','order':3,'question':'소음에 민감하신가요?',
     'options':[{'label':'많이','value':'high'},{'label':'조금','value':'low'},{'label':'괜찮아요','value':'none'}]},
    {'id':'partition','order':4,'question':'칸막이가 있는 자리를 선호하시나요?',
     'options':[{'label':'네','value':'yes'},{'label':'아니오','value':'no'},{'label':'상관없음','value':'no_preference'}]},
]
ALLOWED = {q['id']: {x['value'] for x in q['options']} for q in QUESTION_DEFS}
IDS = list(ALLOWED)
