import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server


class WorkflowTests(unittest.TestCase):
    def test_default_fixed_prompt_is_the_kura_papercut_block(self):
        self.assertTrue(server.DEFAULT_FIXED_PROMPT.startswith('GLOBAL KURA STYLE & RULES'))
        self.assertIn('Realistic tactile paper textures', server.DEFAULT_FIXED_PROMPT)
        self.assertNotIn('subject_definitions:', server.DEFAULT_FIXED_PROMPT)

    def test_fixed_prompt_is_not_duplicated_when_already_in_scene(self):
        fixed = 'GLOBAL KURA STYLE & RULES\nExample style'
        scene = fixed + '\n\nA new scene.'
        wf, _ = server.build_workflow({'prompt': scene, 'fixed_prompt': fixed})
        self.assertEqual(wf['10']['inputs']['value'], scene)
    def test_fixed_prompt_is_prepended_and_scene_duration_is_used(self):
        scene = '[reference generation] An 8.00-second sequence.'
        wf, _ = server.build_workflow({'prompt': scene, 'fixed_prompt': 'STYLE RULES'})
        self.assertEqual(wf['10']['inputs']['value'], 'STYLE RULES\n\n' + scene)
        self.assertEqual(wf['70']['inputs']['length'], 192)

    def test_existing_prompt_without_fixed_prompt_is_unchanged(self):
        wf, _ = server.build_workflow({'prompt': 'my existing full prompt'})
        self.assertEqual(wf['10']['inputs']['value'], 'my existing full prompt')
    def test_advanced_parameters_and_duration(self):
        workflow, seed = server.build_workflow({
            'prompt': 'test', 'seed': 42, 'seconds': 3,
            'advanced': {'denoise': 0.8, 'scheduler': 'normal', 'start_percent': 0.2,
                         'end_percent': 0.9, 'min_tokens': 1024, 'extra_tokens': 128},
        })
        self.assertEqual(seed, 42)
        self.assertEqual(workflow['70']['inputs']['length'], server.frames_for(3))
        self.assertEqual(workflow['75']['inputs']['denoise'], 0.8)
        self.assertEqual(workflow['75']['inputs']['scheduler'], 'normal')
        self.assertEqual(workflow['69']['inputs']['start_percent'], 0.2)
        self.assertEqual(workflow['69']['inputs']['end_percent'], 0.9)
        self.assertEqual(workflow['69']['inputs']['min_tokens'], 1024)
        self.assertEqual(workflow['69']['inputs']['extra_tokens'], 128)

    def test_actual_resolution(self):
        w, h = server.aspect_size('9:16', 0.6)
        self.assertEqual((w, h), (576, 1024))
        self.assertAlmostEqual(w * h / 1e6, 0.589824)

    def test_old_and_new_comfy_combo_schema(self):
        for spec in ([['euler', 'heun']], ['COMBO', {'options': ['euler', 'heun']}]):
            info = {'Node': {'input': {'required': {'sampler': spec}}}}
            self.assertEqual(server.node_choices(info, 'Node', 'sampler'), ['euler', 'heun'])

    def test_reference_generation_duration_precedes_shot_times(self):
        prompt = ('summary:\n[reference generation] An 8.00-second sequence with exactly 4 shots.\n'
                  'detailed_description:\n[Shot 2] At 00:01.800\n[Shot 4] At 00:05.800')
        self.assertEqual(server.parse_duration(prompt), 8.0)
        workflow, _ = server.build_workflow({'prompt': prompt})
        self.assertEqual(workflow['70']['inputs']['length'], 192)

    def test_srt_duration_uses_last_end_timestamp(self):
        scene = '1\n00:00:00,000 --> 00:00:02,500\nPrima scena\n\n2\n00:00:02,500 --> 00:00:08,000\nSeconda scena'
        self.assertEqual(server.parse_duration(scene), 8.0)
        wf, _ = server.build_workflow({'prompt': scene, 'fixed_prompt': server.DEFAULT_FIXED_PROMPT})
        self.assertEqual(wf['70']['inputs']['length'], 192)

    def test_eyes_rule_is_in_the_original_style(self):
        self.assertIn('REGOLA PERMANENTE — EYES', server.DEFAULT_FIXED_PROMPT)
        self.assertIn('NO visible white sclera', server.DEFAULT_FIXED_PROMPT)

    def test_visible_version_has_explicit_release_number(self):
        self.assertEqual(server.APP_VERSION, '2.1.1')

    def test_original_block_without_new_eyes_is_not_duplicated(self):
        old = server.DEFAULT_FIXED_PROMPT.split('\n\nREGOLA PERMANENTE — EYES')[0]
        scene = old + '\n\nA new scene.'
        wf, _ = server.build_workflow({'prompt': scene, 'fixed_prompt': server.DEFAULT_FIXED_PROMPT})
        result = wf['10']['inputs']['value']
        self.assertEqual(result.count('GLOBAL KURA STYLE & RULES'), 1)
        self.assertIn('REGOLA PERMANENTE — EYES', result)
        self.assertIn('A new scene.', result)


if __name__ == '__main__':
    unittest.main()
