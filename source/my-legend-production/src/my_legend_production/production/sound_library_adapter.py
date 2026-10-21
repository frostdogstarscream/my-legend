"""Optional shared library around the pinned SFX adapter, without editing it."""
from hashlib import sha256
from pathlib import Path
import sqlite3

from .sound_library import SoundLibrary, SoundLibraryRequest


def sound_library_adapter(base,root,*,scope,job_id,allow_unreviewed=False):
    library=SoundLibrary(root)

    class CachedSFX(base):
        # The base adapter reserves budget immediately before a real submission.
        # A library hit returns before that call and must not report supplier usage.
        def generate(self,payload,destination):
            request=SoundLibraryRequest(scope=scope,
                provider_endpoint=self.config.get('http_url','https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer'),
                model=self.config.get('model','qwen-audio-3.1-tts-next'),prompt=payload['prompt'],
                seed=payload.get('seed',42),sample_rate=self.config.get('sample_rate',48000),
                channels=self.config.get('channels',1),candidate_version=payload.get('media',{}).get('candidate_version',1))
            hit=None
            try:
                hit=library.lookup(request,allow_unreviewed=allow_unreviewed)
            except (OSError,sqlite3.Error):
                pass # Storage failure must not masquerade as a successful reuse.
            if hit is not None:
                raw=Path(hit['path']).read_bytes()
                if sha256(raw).hexdigest()!=hit['asset_sha256']:
                    raise ValueError('SOUND_LIBRARY_COPY_HASH_MISMATCH')
                destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(raw)
                return dict(origin='sound_library',model=request.model,
                    library_reference=dict(cache_key=hit['cache_key'],asset_sha256=hit['asset_sha256'],
                                           review_state=hit['review_state']),library_status='reused',
                    fixture_only=False)
            result=super().generate(payload,destination)
            # Synthetic/test media must never enter the real shared library.
            if result.get('fixture_only'):
                return dict(result,library_status='fixture_not_published')
            try:
                record=library.publish(request,destination,job_id=job_id,operation_id=Path(destination).stem)
            except (OSError,ValueError,RuntimeError,sqlite3.Error):
                return dict(result,library_status='not_published')
            return dict(result,library_status='published',library_reference={k:record[k] for k in
                        ('cache_key','asset_sha256','review_state')})
    return CachedSFX
