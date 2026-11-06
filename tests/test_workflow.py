import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server


class WorkflowTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
