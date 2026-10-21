"""产物和检查点分离；按计划哈希创建目录，恢复时不重复生成不同版本。"""
import json
from pathlib import Path
import shutil
from .planner import ASSETS, digest

class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def preview(self, plan):
        folder = self.root / 'drafts' / plan['plan_hash']
        folder.mkdir(parents=True, exist_ok=True)
        self.write(folder / 'preview-data.json', plan)
        shutil.copyfile(Path(__file__).with_name('preview.html'), folder / 'index.html')
        shutil.copytree(ASSETS, folder / 'assets', dirs_exist_ok=True)
        if plan.get('preview_skin'):
            pack = 'fogport_painted_v1' if plan['preview_skin']['id'] == 'fogport_painted_v1' else 'fogport_v1'
            shutil.copyfile(Path(__file__).parent / 'assets' / pack / 'characters.png', folder / 'characters.png')
        if plan.get('preview_skin', {}).get('id') in ('fogport_v2', 'fogport_painted_v1'):
            shutil.copyfile(Path(__file__).parent / 'assets' / plan['preview_skin']['id'] / 'background.png', folder / 'background.png')
        return str(folder / 'index.html')

    def freeze(self, request, plan):
        # 评审后再核对素材字节，防止确认期间素材被替换却沿用旧批准。
        import hashlib
        for name, expected in plan['asset_bindings']['file_hashes'].items():
            if hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() != expected:
                from .planner import VisualError
                raise VisualError('ASSET_CHANGED', '素材已变化，需要重新编排和审核', name)
        if plan.get('preview_skin'):
            import hashlib
            pack = 'fogport_painted_v1' if plan['preview_skin']['id'] == 'fogport_painted_v1' else 'fogport_v1'
            custom = Path(__file__).parent / 'assets' / pack / 'characters.png'
            if hashlib.sha256(custom.read_bytes()).hexdigest() != plan['preview_skin']['sha256']:
                from .planner import VisualError
                raise VisualError('ASSET_CHANGED', '生成人物素材变化，需要重新生成')
        if plan.get('preview_skin', {}).get('id') in ('fogport_v2', 'fogport_painted_v1'):
            background = Path(__file__).parent / 'assets' / plan['preview_skin']['id'] / 'background.png'
            if hashlib.sha256(background.read_bytes()).hexdigest() != plan['preview_skin']['background_sha256']:
                from .planner import VisualError
                raise VisualError('ASSET_CHANGED', '新版背景变化，需要重新生成')
        version = digest({'request': request, 'plan_hash': plan['plan_hash']})
        folder = self.root / 'versions' / version
        folder.mkdir(parents=True, exist_ok=True)
        if plan.get('preview_skin'):
            shutil.copyfile(custom, folder / 'characters.png')
            self.write(folder / 'web-skin.json', plan['preview_skin'])
        if plan.get('preview_skin', {}).get('id') in ('fogport_v2', 'fogport_painted_v1'):
            shutil.copyfile(background, folder / 'background.png')
        self.write(folder / 'runtime.json', plan['document'])
        self.write(folder / 'provenance.json', {'visual_version_id': version,
            'request': request, 'plan_hash': plan['plan_hash'],
            'coverage': plan['coverage'], 'asset_bindings': plan['asset_bindings'],
            'validation_report': plan['validation_report'],
            'preview_skin':plan.get('preview_skin'),
            'generation_issues':plan.get('generation_issues', []),
            'generation_mode':plan.get('generation_mode', 'reviewed'),
            'degradations':plan.get('degradations', []),
            'presentation_policy_version':plan.get('presentation_policy_version'),
            'presentation_profile':plan.get('presentation_profile', 'legacy_basic'),
            'source_hash':plan.get('source_hash'), 'source_snapshot':plan.get('source_snapshot'),
            'resolved_source_snapshot':plan.get('resolved_source_snapshot'),
            'target_capabilities':plan.get('target_capabilities')})
        self.write(folder / 'manifest.json', plan['manifest'])
        return {'visual_version_id': version, 'runtime_package_ref': str(folder / 'runtime.json')}

    @staticmethod
    def write(path, data):
        # 临时文件再替换，失败时不留下半份 JSON；目录内容不含用户提供的路径。
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
