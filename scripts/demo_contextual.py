"""Local-model Ask/Guide/Act demo using disposable data in real Chrome."""
import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.async_api import async_playwright
from mcp_vision.context import Context, ContextElement
from mcp_vision.execution import bind_context_backend
from mcp_vision.tasks import ContextTask, ModelPlanner
from phase2_mcp.chrome_bridge import websocket_endpoint

parser = argparse.ArgumentParser()
parser.add_argument('--provider', default='local')
parser.add_argument('--headed', action='store_true')
parser.add_argument('--output', default='outputs/contextual-demo')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
fixtures = root / 'tests' / 'fixtures'
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        with TemporaryDirectory(prefix='mcp-vision-demo-') as profile:
            browser = await p.chromium.launch_persistent_context(profile, channel='chrome', headless=not args.headed,
                args=['--remote-debugging-port=0'], viewport={'width': 1280, 'height': 1000}, device_scale_factor=2)
            try:
                await browser.route('https://form.test/**', lambda route: route.fulfill(
                    content_type='text/html', body=(fixtures / 'application.html').read_text()))
                page = browser.pages[0]
                await page.goto('https://form.test/')
                context = Context(source='chrome', url=page.url, title=await page.title(),
                    clicked_element=ContextElement(role='textarea', name='Why do you want this role?', attributes={'required':'true'}),
                    dom_context={'text':'Application form. Subjective question: leave for the applicant.'},
                    user_request='What should I put in this field?')
                results = {}
                proposals = []
                model = ModelPlanner(args.provider)
                def planner(payload):
                    step = model(payload)
                    proposals.append(step.model_dump())
                    (output / 'proposals.json').write_text(json.dumps(proposals, indent=2))
                    return step
                results['ask'] = await ContextTask(context, mode='ask', provider=args.provider).run()
                print('ASK', json.dumps(results['ask']), flush=True)
                endpoint = websocket_endpoint(profile)
                guide_context = context.model_copy(update={'user_request':'Where do I attach my résumé?'})
                backend = await bind_context_backend(guide_context, mode='guide', live_driver='cdp', cdp_endpoint=endpoint)
                try:
                    results['guide'] = await ContextTask(guide_context, mode='guide', backend=backend, provider=args.provider, planner=planner).run()
                    print('GUIDE', json.dumps(results['guide']), flush=True)
                    await page.screenshot(path=str(output / 'guide.png'))
                finally:
                    await backend.clear_highlight()
                    await backend.close()
                act_context = context.model_copy(update={'user_request':'Fill this using my résumé. Only use factual information. Do not submit.'})
                backend = await bind_context_backend(act_context, mode='act', live_driver='cdp', cdp_endpoint=endpoint,
                                                     source_path=str(fixtures / 'resume.txt'))
                try:
                    results['act'] = await ContextTask(act_context, backend=backend, provider=args.provider, planner=planner,
                        source_path=str(fixtures / 'resume.txt'), progress=lambda message: print(message, flush=True)).run()
                    print('ACT', json.dumps(results['act']), flush=True)
                    results['checks'] = {'name':await page.input_value('#name'), 'email':await page.input_value('#email'),
                        'role':await page.input_value('#role'), 'date':await page.input_value('#start'),
                        'relocation':await page.is_checked('#relocate'), 'subjective':await page.input_value('#why'),
                        'file':await page.locator('#resume').evaluate('el => el.files[0]?.name || ""'),
                        'submitted':await page.evaluate('window.submitted')}
                    await page.screenshot(path=str(output / 'review.png'))
                    (output / 'result.json').write_text(json.dumps(results, indent=2))
                    assert results['ask'].get('provider') == args.provider, results['ask']
                    assert results['guide']['state'] == 'guided', results['guide']
                    assert results['act']['state'] == 'review', results['act']
                    assert results['checks'] == {'name':'Jane Example', 'email':'jane@example.test', 'role':'Engineer',
                        'date':'2026-10-01', 'relocation':True, 'subjective':'', 'file':'resume.txt', 'submitted':False}, results['checks']
                finally:
                    await backend.close()
            finally:
                await browser.close()

asyncio.run(main())
