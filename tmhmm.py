import collections
import itertools
import re

import numpy as np

from viterbi import viterbi


def _tokenize(contents):
    return re.findall(r'([A-Za-z0-9\.\-_]+|[:;\{\}])', contents)


def _strip_comments(file_like):
    return ''.join(filter(lambda l: not l.startswith('#'), file_like))


def _parse_list(tokens):
    parsed_list = []
    while True:
        token = tokens.popleft()
        if token == ';':
            tokens.appendleft(token)
            return tokens, parsed_list
        parsed_list.append(token)


def _parse_map(tokens):
    parsed_map = collections.OrderedDict()
    while True:
        token = tokens.popleft()
        if token == ';':
            tokens.appendleft(token)
            return tokens, parsed_map
        next_token = tokens.popleft()

        # Fallback if the map was actually a list
        if next_token != ':':
            tokens.appendleft(next_token)
            tokens.appendleft(token)
            return tokens, None

        value = tokens.popleft()
        parsed_map[token] = float(value)



def _parse_state(tokens):
    state_name = tokens.popleft()
    tokens.popleft() # "{"

    parsed_state = {}
    while True:
        token = tokens.popleft()
        if token == '}':
            return tokens, (state_name, parsed_state)
        if token in ('trans', 'only'):
            tokens, value = _parse_map(tokens)
            if value is None:
                tokens, value = _parse_list(tokens)
        elif token in ('type', 'end'):
            value = int(tokens.popleft())
        else:
            value = tokens.popleft()
        parsed_state[token] = value
        tokens.popleft() # ";"


def _parse_header(tokens):
    tokens.popleft() # "header"
    tokens.popleft() # "{"

    header = {}
    while True:
        token = tokens.popleft()
        if token == '}':
            break
        header[token] = tokens.popleft()
        tokens.popleft() # ";"
    return tokens, header


def _normalize_states(states):
    """
    The TMHMM file format allows parameters to be tied to the parameters of
    some other state. This basically means that a state inherits the parameters
    from another state.

    The normalization performed by this function consists of copying the
    specified parameters from the parent state to the inheriting state such
    that all states explicitly specify their transition and emission
    probabilities.
    """
    for name, state in states.items():
        # inherit parent's transition probabilities, but only for
        # the states specified for this state.
        if 'tied_trans' in state:
            parent_state = states[state['tied_trans']]
            to_states = state['trans']
            states[name]['trans'] = dict(zip(state['trans'],
                                         parent_state['trans'].values()))

        # inherit parent's emission probabilities
        if 'tied_letter' in state:
            parent_state = state['tied_letter']
            states[name]['only'] = dict(states[parent_state]['only'])
    return states


def _to_matrix_form(alphabet, states):
    """
    Convert a model to matrix form.
    """
    # pull out initial probabilities
    begin = dict(states['begin'])
    del states['begin']

    # build state -> index mapping
    state_map = {v: k for k, v in enumerate(states)}
    # build character -> index mapping
    char_map = {v: k for k, v in enumerate(alphabet)}

    no_states = len(states)

    initial = np.zeros(shape=(no_states,))
    transitions = np.zeros(shape=(no_states, no_states))
    emissions = np.zeros(shape=(no_states, len(alphabet)))

    label_map = {}
    name_map = dict(enumerate(states))

    # initial probabilities
    for state_name, trans_prob in begin['trans'].items():
        this_state_idx = state_map[state_name]
        initial[this_state_idx] = trans_prob

    for state_name, state in states.items():
        this_state_idx = state_map[state_name]

        # label map
        if 'label' in state:
            label_map[this_state_idx] = state['label']

        # transition probabilities
        for other_state_name, trans_prob in state['trans'].items():
            other_state_idx = state_map[other_state_name]
            transitions[this_state_idx, other_state_idx] = trans_prob

        # emission probabilities
        for character, emission_prob in state['only'].items():
            this_character_idx = char_map[character]
            emissions[this_state_idx, this_character_idx] = emission_prob

    return initial, transitions, emissions, char_map, label_map, name_map


def parse_model(file_like):
    """
    Parse a model in the TMHMM 2.0 format.

    :param file_like: a file-like object to read and parse.
    :return: a model
    """
    contents = _strip_comments(file_like)
    tokens = collections.deque(_tokenize(contents))

    tokens, header = _parse_header(tokens)

    states = {}
    while tokens:
        tokens, (name, state) = _parse_state(tokens)
        states[name] = state

    assert not tokens, "list of tokens not consumed completely"
    return header, _to_matrix_form(header['alphabet'],
                                   _normalize_states(states))


def summarize(path):
    """
    Summarize a path as a list of (start, end, state) triples.
    """
    for state, group in itertools.groupby(enumerate(path), key=lambda x: x[1]):
        group = list(group)
        start = min(group, key=lambda x: x[0])[0]
        end = max(group, key=lambda x: x[0])[0]
        yield start, end, state


if __name__ == '__main__':
    import argparse

    import skbio.io


    PRETTY_NAMES = {
        'i': 'inside',
        'M': 'transmembrane helix',
        'o': 'outside',
        'O': 'outside'
    }

    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--file', dest='sequence_file',
                        type=argparse.FileType('r'), required=True,
                        help='path to file in fasta format with sequences')
    parser.add_argument('-m', '--model', dest='model_file',
                        type=argparse.FileType('r'), default='TMHMM2.0.model',
                        help='path to the model to use')

    args = parser.parse_args()

    header, model = parse_model(args.model_file)
    for record in skbio.io.read(args.sequence_file, format='fasta'):
        for _ in range(10):
            matrix, path = viterbi(record.sequence, *model)
        for start, end, state in summarize(path):
            print("{} {} {}".format(start, end, PRETTY_NAMES[state]))
        print()
        print('>', record.id, ' ', record.description, sep='')
        print(path)
