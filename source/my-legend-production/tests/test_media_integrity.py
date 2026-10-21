from pathlib import Path

import pytest
import soundfile as sf
import numpy as np

from my_legend_production.core import AdapterError
from my_legend_production.local_demo import padded_mix,signal


@pytest.mark.parametrize('subtype',['PCM_16','PCM_24','FLOAT'])
def test_explicit_silence_tail_preserves_audio_and_rejects_changed_derivative(tmp_path,subtype):
    source=tmp_path/'source.wav';dest=tmp_path/'derived.wav'
    sf.write(source,.1234567*np.sin(np.arange(4800)*.1153),24000,subtype=subtype)
    info=padded_mix(source,dest,500)
    a,rate=sf.read(source);b,out_rate=sf.read(dest)
    assert rate==out_rate and len(b)==rate//2 and np.array_equal(a,b[:len(a)])
    assert sf.info(dest).subtype==subtype
    assert np.count_nonzero(b[len(a):])==0
    assert padded_mix(source,dest,500)==info
    sf.write(dest,np.zeros(len(b)),rate,subtype='PCM_16')
    with pytest.raises(AdapterError,match='DERIVED_MEDIA_CHANGED'):padded_mix(source,dest,500)
