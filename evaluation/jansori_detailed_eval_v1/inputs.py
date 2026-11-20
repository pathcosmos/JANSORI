import hashlib,json,random
from pathlib import Path
from tokenizers import Tokenizer
d=Path(__file__).resolve().parent
release=d.parent/'3b_47k_hub_release_v1'
pin=json.loads((release/'jansori-hub-v1/public-snapshot.json').read_text())
t=Tokenizer.from_file(str(Path(pin['local_path'])/'tokenizer.json'))
items=[]
def add(tag,lang,prompt,**extra):
    ids=t.encode(prompt).ids
    items.append(dict(id=f'{tag}-{lang}-{len(items):03}',tag=tag,lang=lang,prompt=prompt,ids=ids,**extra))
facts={
'ko': [('물의 화학식은?','H2O|H₂O'),('지구의 자연 위성은?','달'),('이탈리아의 수도는?','로마'),('한 주는 며칠인가?','7일|7|칠 일|일주일'),('정삼각형의 세 내각은 각각 몇 도인가?','60도|60|육십 도'),('동물 중 가장 큰 포유류는?','대왕고래|흰긴수염고래'),('한글 창제를 주도한 조선의 왕은?','세종|세종대왕'),('태양계에서 태양과 가장 가까운 행성은?','수성'),('지구 대기에서 가장 많은 기체는?','질소'),('태양계에서 가장 큰 행성은?','목성'),('사람 심장의 방은 몇 개인가?','4개|4|네 개'),('식물이 광합성에 사용하는 빛을 흡수하는 초록색 색소는?','엽록소')],
'en':[('What is the chemical symbol for gold?','Au'),('What is the capital of Japan?','Tokyo'),('Which ocean is the largest?','Pacific|Pacific Ocean|the Pacific Ocean'),('How many sides does a hexagon have?','six|6'),('Which planet is known as the red planet?','Mars'),('Which gas do humans need for aerobic respiration?','oxygen|O2'),('Which metal is liquid at ordinary room temperature?','mercury'),('What is the smallest prime number?','2|two'),('In which organ are the alveoli?','lungs|the lungs'),('What is the capital of Germany?','Berlin'),('How many minutes are in an hour?','60|sixty'),('Which organ pumps blood through the body?','heart|the heart')]}
prefix={'ko':'질문: 프랑스의 수도는?\n짧은 답: 파리\n질문: 일 년은 몇 달인가?\n짧은 답: 12개월\n','en':'Question: What is the capital of France?\nShort answer: Paris\nQuestion: How many months are in a year?\nShort answer: 12\n'}
for lang,qs in facts.items():
    for q,a in qs:add('fact',lang,prefix[lang]+('질문: '+q+'\n짧은 답:' if lang=='ko' else 'Question: '+q+'\nShort answer:'),answers=a.split('|'))
for lang in ['ko','en']:
    for i in range(8):
        if lang=='ko':
            who=['지수','현우','민서','서진','유나','하준','지안','소라'][i]
            obj=['파랑','주황','초록','보라','노랑','분홍','빨강','갈색'][i]
            context=f'{who}는 {obj} 상자에 표를 넣었다. 다른 상자에는 표가 없다.'
            filler=' 버스는 오후에 도착했다. 창고의 빈 의자를 복도로 옮겼다.'
            q=f'{who}가 표를 넣은 상자의 색은?'
            prompt=prefix[lang]+(filler*(9 if i%2 else 0))+context+'\n질문: '+q+'\n짧은 답:'
        else:
            who=['Mara','Leo','Nora','Vera','Iris','Owen','Dana','Kira'][i]
            obj=['green','black','white','yellow','orange','purple','blue','red'][i]
            context=f'{who} put the ticket inside the {obj} box. The other boxes were empty.'
            filler=' Outside, the bus went by. The empty chair had been moved to the hallway.'
            prompt=prefix[lang]+filler*(9 if i%2 else 0)+context+'\nQuestion: What color was the box holding the ticket?\nShort answer:'
        add('reading',lang,prompt,answers=[obj],length_band='long' if i%2 else 'short')
stories={'ko':['새벽에 빵집 문을 열자 밀가루 포대 옆에서','비가 멈춘 뒤 운동장 한가운데 남아 있던 것은','서랍에서 발견한 오래된 편지에는','눈 덮인 역에서 마지막 기차를 기다리던 나는'],
'en':['At dawn, the baker opened the door and saw','When the rain stopped, the only thing left on the field was','Inside the old envelope she found','At the snowy station, the last train']}
for lang,ss in stories.items():
    for i,s in enumerate(ss):
        background=(' 겨울 기록을 정리하던 사람이 그날의 일을 떠올렸다.' if lang=='ko' else ' Years later, the witness remembered that day in detail.')*4
        add('story',lang,(background.strip()+'\n' if i%2 else '')+s,length_band='long' if i%2 else 'short')
rng=random.Random(47000)
for i in range(32):
    lang='en' if i%2 else 'ko'
    a,b,c=rng.randint(3,18),rng.randint(2,9),rng.randint(2,8)
    j=(i//2)%4
    expr,answer=[(f'{a} + {b}',a+b),(f'{a} × {b}',a*b),(f'({a} + {b}) × {c}',(a+b)*c),(f'({a*b} ÷ {b}) - {c}',a-c)][j]
    p=('계산식의 값을 정수로 쓴다.\n8 + 3 = 11\n6 × 2 = 12\n' if lang=='ko' else 'Write the result as an integer.\n8 + 3 = 11\n6 × 2 = 12\n')+expr+' ='
    add('math',lang,p,answer=answer,level='basic' if j<2 else 'two_step')
code=[('square','x','Return the square of x.',lambda x:x*x,[-4,0,2,5]),
      ('even','x','Return True for even integers and False for odd integers.',lambda x:x%2==0,[-3,0,4,7]),
      ('last_item','xs','Return the last item of the nonempty list xs.',lambda x:x[-1],[[1],[3,-2],[0,8,4],['a','b']]),
      ('reverse_text','s','Return the string s in reverse order.',lambda s:s[::-1],['','abc','한글','AB1Z']),
      ('absolute','n','Return the absolute value of n.',abs,[-7,0,1,12]),
      ('double_values','xs','Return a list with each item of xs multiplied by two.',lambda x:[2*i for i in x],[[],[0],[1,-2],[3,4,0]]),
      ('count_zeros','xs','Return the number of zeros in xs.',lambda x:x.count(0),[[],[0,1,0],[3],[0,0,0]]),
      ('triangle','n','Return the sum of integers from 1 through n inclusive. n is nonnegative.',lambda n:n*(n+1)//2,[0,1,4,10]),
      ('positive_only','xs','Return a list containing only strictly positive items, in their original order.',lambda x:[i for i in x if i>0],[[],[-1,0],[4,0,1],[-3,2]]),
      ('largest','xs','Return the largest element of the nonempty list xs.',max,[[2],[-5,-2],[9,0,4],[3,3]]),
      ('first_or_zero','xs','Return the first item, or zero when xs is empty.',lambda x:x[0] if x else 0,[[],[8],[2,7],[-1,0]]),
      ('odd','n','Return a boolean indicating whether n is odd.',lambda n:n%2!=0,[-5,0,1,8])]
for name,argument,desc,func,values in code:
    header=f'def {name}({argument}):\n    """{desc}"""\n'
    add('code','python',header,tests=[[x,func(x)] for x in values],name=name)
for row in json.loads((release/'nfs-release-v1/evidence/fixed-generations.json').read_text())['outputs']['BF16']:
    add('regression','mixed',row['prompt'])
(d/'quality-inputs.json').write_text(json.dumps(items,ensure_ascii=False,indent=1))
# Deterministic, varied text; every word below is exactly one tokenizer ID including its leading space.
words=[x for x in 'red blue gold book chair house quiet summer autumn face grey sweet five twelve apple green open closed tree water dark light paper long cloud river time day table small dog cat room school language city morning street wheel train plain stone wide hand clear book ball'.split() if len(t.encode(' '+x).ids)==1]
assert len(set(words))>=30
# BOS handling is calibrated per runtime using /tokenize or an independent prompt counter.
def perf_input(seed,n):
    r=random.Random(seed)
    text=''.join(' '+r.choice(words) for _ in range(n))
    assert len(t.encode(text).ids)==n
    return text
if __name__=='__main__':print('quality',len(items),'performance words',len(words))
