import asyncio,copy,importlib.util,json,pathlib,time,unittest,sys
root=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('bible_plugin',root/'plugin.py');p=importlib.util.module_from_spec(spec);sys.modules[spec.name]=p;spec.loader.exec_module(p)

class MatchTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.index=p.VerseIndex(root/'assets/verses.json')
 def msg(self,text):return {'message_id':'test','platform':'qq','message_info':{'group_info':{'group_id':'fixture','group_name':'fixture'},'additional_config':{'preserve':'yes'}},'raw_message':[{'type':'text','data':text}],'processed_plain_text':text,'account_id':'fixture','reply_to':'fixture'}
 def test_known_sentence(self):self.assertEqual(self.index.find('日光之下并无新事。'),['传道书 1:9'])
 def test_complete_verse(self):self.assertEqual(self.index.find('起初，神创造天地。'),['创世记 1:1'])
 def test_spaces_quotes_punctuation(self):self.assertEqual(self.index.find('“ 日光 之下并无新事！”'),['传道书 1:9'])
 def test_real_short_sentence(self):self.assertTrue(self.index.find('不要害怕。'))
 def test_unmatched_and_short(self):
  for s in ['好的','是的','干嘛','今晚吃火锅还是烧烤呀']:
   self.assertEqual(self.index.find(s),[])
 def test_no_semantic_matching(self):self.assertNotIn('传道书 1:9',self.index.find('太阳底下没有什么新鲜事'))
 def test_whole_sentence_only(self):self.assertEqual(self.index.find('我觉得日光之下并无新事但还是吃饭吧',partial_match=False),[])
 def test_sentence_in_reply(self):self.assertEqual(self.index.find('吃饭去。日光之下并无新事。'),['传道书 1:9'])
 def test_sentence_toggle(self):self.assertEqual(self.index.find('吃饭去。日光之下并无新事。',match_sentences=False,partial_match=False),[])
 def test_no_cross_verse(self):
  a,b=self.index.rows[:2];token=p.normalize(a['text'])[-4:]+p.normalize(b['text'])[:5]
  self.assertEqual(self.index.find(token,min_chars=len(token)),[])
 def test_no_quote_or_image_ocr(self):
  m=self.msg('晚饭去哪吃');m['raw_message'] += [{'type':'reply','data':{'text':'日光之下并无新事'}},{'type':'image','data':'日光之下并无新事','binary_data':'fixture'}]
  self.assertIsNone(p.annotate_message(m,self.index,p.Settings())[0])
 def test_preserve_metadata_and_media(self):
  m=self.msg('日光之下并无新事。');m['raw_message'].insert(0,{'type':'at','data':{'target_user_id':'fixture'}});m['raw_message'].append({'type':'image','data':'fixture','binary_data':'AA=='})
  before=copy.deepcopy(m);out,refs=p.annotate_message(m,self.index,p.Settings());self.assertEqual(m,before)
  self.assertEqual(out['raw_message'][:-1],before['raw_message']);self.assertEqual(out['raw_message'][-1],{'type':'text','data':'\n--出自（传道书 1:9）'})
  for k in m:
   if k not in ('raw_message','processed_plain_text'):self.assertEqual(m[k],out[k])
  self.assertIsNone(p.annotate_message(out,self.index,p.Settings())[0])
 def test_adjacent_segments(self):
  m=self.msg('日光之下');m['raw_message'].append({'type':'text','data':'并无新事。'});self.assertIsNotNone(p.annotate_message(m,self.index,p.Settings())[0])
 def test_not_private_or_other_platform(self):
  m=self.msg('日光之下并无新事');m['message_info']['group_info']=None
  self.assertIsNone(p.annotate_message(m,self.index,p.Settings())[0]);self.assertIsNotNone(p.annotate_message(m,self.index,p.Settings(group_only=False))[0]);m['platform']='internal';self.assertIsNone(p.annotate_message(m,self.index,p.Settings(group_only=False))[0])
 def test_disable_notify_command(self):
  for key in ['is_notify','is_command']:
   m=self.msg('日光之下并无新事');m[key]=True;self.assertIsNone(p.annotate_message(m,self.index,p.Settings())[0])
  self.assertIsNone(p.annotate_message(self.msg('日光之下并无新事'),self.index,p.Settings(enabled=False))[0])
 def test_multiple_refs_capped(self):
  refs=self.index.find('起初神创造天地。日光之下并无新事。',max_references=2);self.assertEqual(len(refs),2);self.assertIn('创世记 1:1',refs);self.assertIn('传道书 1:9',refs)
 def test_code_and_url_not_cited(self):
  self.assertEqual(self.index.find('```\n日光之下并无新事\n```'),[]);self.assertEqual(self.index.find('https://example.com/日光之下并无新事'),[])
 def test_partial_fragment(self):
  self.assertEqual(self.index.find('那“给我水喝”也算命中啊'),['创世记 24:14'])
  self.assertEqual(self.index.find('可以给我水喝吗'),['创世记 24:14'])
  self.assertEqual(self.index.find('我觉得日光之下并无新事但还是吃饭吧'),['传道书 1:9'])
 def test_partial_toggle(self):
  self.assertEqual(self.index.find('那“给我水喝”也算命中啊',partial_match=False),[])
 def test_no_cross_media_boundary(self):
  m=self.msg('给我');m['raw_message'] += [{'type':'image','data':'fixture'},{'type':'text','data':'水喝'}]
  self.assertIsNone(p.annotate_message(m,self.index,p.Settings())[0])
 def test_minimum_three_configurable(self):
  self.assertEqual(self.index.find('要有光'),[])
  self.assertEqual(self.index.find('要有光',min_chars=3),['创世记 1:3'])
 def test_adaptive_five_char_threshold(self):
  for text in ['给我水喝','要有光','有光','光']:
   self.assertIsNotNone(p.annotate_message(self.msg(text),self.index,p.Settings())[0])
  for text in ['那给我水喝也算命中啊','喵喵','！！！','   ']:
   self.assertIsNone(p.annotate_message(self.msg(text),self.index,p.Settings())[0])
  self.assertIsNotNone(p.annotate_message(self.msg('那请你给我水喝也算命中啊'),self.index,p.Settings())[0])
  self.assertIsNotNone(p.annotate_message(self.msg('“ 给我水喝！”'),self.index,p.Settings())[0])
 def test_lookup_only_punctuation_normalization(self):
  self.assertEqual(p.normalize('“嗨！”你在吗？hello,world.好，没事。'), '嗨你在吗hello，world。好，没事。')
  self.assertEqual(p.meaningful_length(p.normalize('给我水喝。')),4)
 def test_original_reply_preserved(self):
  async def run():
   plugin=p.BibleCitationPlugin();plugin.set_plugin_config({'filter_punctuation':True,'plugin':{'config_version':'1.1.0'}});await plugin.on_load()
   for text in ['喵喵喵？！','？！','“请你给我水喝！”','看 https://example.com/a?q=1&v=2','**喵喵喵**\n（真的？）']:
    original=self.msg(text);result=await plugin.before_send(message=original,typing=False,reply_message_id='fixture')
    self.assertEqual(result['action'],'continue')
    if 'modified_kwargs' in result:
     sent=result['modified_kwargs']['message'];self.assertEqual(sent['raw_message'][0]['data'],text)
     self.assertTrue(sent['processed_plain_text'].startswith(text+'\n--出自（'))
     self.assertIs(result['modified_kwargs']['typing'],False)
     self.assertEqual(result['modified_kwargs']['reply_message_id'],'fixture')
    else:self.assertEqual(original,self.msg(text))
   self.assertIsNone(p.annotate_message(self.msg('那给我水喝。也算命中啊'),self.index,p.Settings())[0])
   await plugin.on_unload()
  asyncio.run(run())
 def test_source_integrity(self):
  self.assertEqual(len(self.index.rows),31021);self.assertEqual(len({r['book']for r in self.index.rows}),66)
  self.assertEqual(self.index.rows[-1]['book'],'启示录');self.assertEqual(self.index.rows[-1]['chapter'],22);self.assertEqual(self.index.rows[-1]['verse'],21)
 def test_invalid_payload(self):
  for m in [None,{},self.msg('')]:self.assertIsNone(p.annotate_message(m,self.index,p.Settings())[0])
 def test_hook_and_widget(self):
  async def run():
   plugin=p.BibleCitationPlugin();plugin.set_plugin_config({});await plugin.on_load();await plugin.on_config_widget_action('self_test')
   result=await plugin.before_send(message=self.msg('日光之下并无新事。'),typing=False)
   self.assertEqual(result['modified_kwargs']['message']['raw_message'][-1]['data'],'\n--出自（传道书 1:9）');self.assertIs(result['modified_kwargs']['typing'],False)
   self.assertEqual((await plugin.get_config_widget_state())['status'].tone,'success');await plugin.on_unload()
  asyncio.run(run())

if __name__=='__main__':
 unittest.main(verbosity=2)
