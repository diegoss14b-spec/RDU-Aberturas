"""Render the actual Ops JS: initial unseen is not remaining work."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_actual_renderer_distinguishes_selected_attempted_and_outside_selection():
    node = os.environ.get('RDU_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for JS rendering regression')
    source = str(Path(__file__).parent/'valor/js/ops.js')
    script = r'''
const fs=require('fs'), vm=require('vm');
const root={innerHTML:''};
const context={window:{setInterval() {}, OPS:{casas:[{
  nome:'bet365',id:'bet365',ok:false,error:'failed "quoted" <unsafe>',
  discovery:{inventory:995,selected:120,attempted:60,not_selected:875,unseen:995}
}]}},document:{getElementById(){return root;}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.window.renderOps();
process.stdout.write(JSON.stringify(root.innerHTML));
'''
    html = json.loads(subprocess.check_output([node,'-e',script,source],text=True))
    assert 'Inventário: 995 · selecionados: 120 · consultados: 60 · fora da seleção: 875' in html
    assert 'não visitados: 995' not in html
    assert 'title="failed &quot;quoted&quot; &lt;unsafe&gt;' in html
    assert '<unsafe>' not in html
