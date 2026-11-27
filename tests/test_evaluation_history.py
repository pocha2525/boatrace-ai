import json
import pandas as pd
import evaluation


def test_explored_results_cannot_be_reused_for_promotion(tmp_path):
    model = tmp_path/'model'
    model.mkdir()
    history = tmp_path/'logs'/'refine'/'experiment'
    history.mkdir(parents=True)
    (history/'summary.json').write_text(json.dumps({'holdout_until': 20261126}))
    frame = pd.DataFrame({'date': [20261125, 20261126, 20261127]})
    assert evaluation.fresh_rows(frame, model/'evaluation_state.json', 20261120).date.tolist() == [20261127]
    evaluation.consume(model/'evaluation_state.json', 20261127)
    assert evaluation.fresh_rows(frame, model/'evaluation_state.json', 20261120).empty
